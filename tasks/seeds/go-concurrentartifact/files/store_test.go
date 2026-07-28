package artifact

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

func digestOf(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func TestSequentialPublishAndRead(t *testing.T) {
	root := t.TempDir()
	store := NewStore(root)
	payload := []byte("a complete build artifact\n")
	digest := digestOf(payload)

	result, err := store.Publish(context.Background(), digest, bytes.NewReader(payload))
	if err != nil {
		t.Fatalf("Publish: %v", err)
	}
	if !result.Created || result.Size != int64(len(payload)) {
		t.Fatalf("first result = %+v", result)
	}
	if want := filepath.Join(root, digest[:2], digest[2:]); result.Path != want {
		t.Fatalf("path = %q, want %q", result.Path, want)
	}

	got, err := store.Read(context.Background(), digest)
	if err != nil {
		t.Fatalf("Read: %v", err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatalf("Read returned %q, want %q", got, payload)
	}
	assertOnlyObjects(t, root, digest)
}

type channelGateReader struct {
	once    sync.Once
	ready   chan<- struct{}
	release <-chan struct{}
	reader  *bytes.Reader
}

func (r *channelGateReader) Read(p []byte) (int, error) {
	r.once.Do(func() {
		r.ready <- struct{}{}
		<-r.release
	})
	return r.reader.Read(p)
}

func TestConcurrentIndependentStoresHaveOneCreator(t *testing.T) {
	const publishers = 12
	root := t.TempDir()
	payload := bytes.Repeat([]byte("concurrent artifact\n"), 2048)
	digest := digestOf(payload)
	ready := make(chan struct{}, publishers)
	release := make(chan struct{})

	type outcome struct {
		result PublishResult
		err    error
	}
	outcomes := make(chan outcome, publishers)
	for i := 0; i < publishers; i++ {
		go func() {
			source := &channelGateReader{
				ready:   ready,
				release: release,
				reader:  bytes.NewReader(payload),
			}
			result, err := NewStore(root).Publish(context.Background(), digest, source)
			outcomes <- outcome{result: result, err: err}
		}()
	}

	for i := 0; i < publishers; i++ {
		select {
		case <-ready:
		case <-time.After(5 * time.Second):
			t.Fatal("publishers did not all reach their source reads")
		}
	}
	close(release)

	created := 0
	for i := 0; i < publishers; i++ {
		out := <-outcomes
		if out.err != nil {
			t.Fatalf("Publish: %v", out.err)
		}
		if out.result.Created {
			created++
		}
	}
	if created != 1 {
		t.Fatalf("Created=true count = %d, want exactly 1", created)
	}

	got, err := NewStore(root).Read(context.Background(), digest)
	if err != nil {
		t.Fatalf("Read: %v", err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatal("published artifact differs from source")
	}
	assertOnlyObjects(t, root, digest)
}

type pausingReader struct {
	once    sync.Once
	started chan<- struct{}
	release <-chan struct{}
	reader  *bytes.Reader
}

func (r *pausingReader) Read(p []byte) (int, error) {
	r.once.Do(func() {
		close(r.started)
		<-r.release
	})
	return r.reader.Read(p)
}

func TestRacingLoserCannotReplaceWinner(t *testing.T) {
	root := t.TempDir()
	payload := bytes.Repeat([]byte("stable winner bytes\n"), 4096)
	digest := digestOf(payload)
	started := make(chan struct{})
	release := make(chan struct{})
	candidateDone := make(chan struct {
		result PublishResult
		err    error
	}, 1)

	go func() {
		result, err := NewStore(root).Publish(context.Background(), digest, &pausingReader{
			started: started,
			release: release,
			reader:  bytes.NewReader(payload),
		})
		candidateDone <- struct {
			result PublishResult
			err    error
		}{result, err}
	}()

	select {
	case <-started:
	case <-time.After(5 * time.Second):
		t.Fatal("candidate did not reach source read")
	}

	winner, err := NewStore(root).Publish(context.Background(), digest, bytes.NewReader(payload))
	if err != nil {
		t.Fatalf("winning Publish: %v", err)
	}
	if !winner.Created {
		t.Fatal("independent winner did not create artifact")
	}
	pinned, err := os.Open(winner.Path)
	if err != nil {
		t.Fatalf("open winner: %v", err)
	}
	defer pinned.Close()
	pinnedInfo, err := pinned.Stat()
	if err != nil {
		t.Fatalf("stat open winner: %v", err)
	}

	close(release)
	out := <-candidateDone
	if out.err != nil {
		t.Fatalf("candidate Publish: %v", out.err)
	}
	if out.result.Created {
		t.Fatal("racing loser reported Created=true")
	}
	pathInfo, err := os.Stat(winner.Path)
	if err != nil {
		t.Fatalf("stat final path: %v", err)
	}
	if !os.SameFile(pinnedInfo, pathInfo) {
		t.Fatal("racing candidate replaced the winning filesystem object")
	}
	assertOnlyObjects(t, root, digest)
}

type countingReader struct {
	reader *bytes.Reader
	read   int
}

func (r *countingReader) Read(p []byte) (int, error) {
	n, err := r.reader.Read(p)
	r.read += n
	return n, err
}

func TestExistingObjectDoesNotSkipCandidateVerification(t *testing.T) {
	root := t.TempDir()
	store := NewStore(root)
	payload := []byte("the expected artifact")
	digest := digestOf(payload)
	if _, err := store.Publish(context.Background(), digest, bytes.NewReader(payload)); err != nil {
		t.Fatalf("seed Publish: %v", err)
	}

	t.Run("valid duplicate is fully consumed", func(t *testing.T) {
		source := &countingReader{reader: bytes.NewReader(payload)}
		result, err := store.Publish(context.Background(), digest, source)
		if err != nil {
			t.Fatalf("duplicate Publish: %v", err)
		}
		if result.Created {
			t.Fatal("duplicate reported Created=true")
		}
		if source.read != len(payload) {
			t.Fatalf("source bytes read = %d, want %d", source.read, len(payload))
		}
	})

	t.Run("mismatching duplicate is rejected", func(t *testing.T) {
		wrong := []byte("not the expected artifact")
		source := &countingReader{reader: bytes.NewReader(wrong)}
		_, err := store.Publish(context.Background(), digest, source)
		if !errors.Is(err, ErrDigestMismatch) {
			t.Fatalf("error = %v, want ErrDigestMismatch", err)
		}
		if source.read != len(wrong) {
			t.Fatalf("source bytes read = %d, want %d", source.read, len(wrong))
		}
	})

	assertOnlyObjects(t, root, digest)
}

func TestCorruptExistingObjectIsNeverTrustedOrRepaired(t *testing.T) {
	root := t.TempDir()
	store := NewStore(root)
	payload := []byte("authentic artifact contents")
	digest := digestOf(payload)
	path := filepath.Join(root, digest[:2], digest[2:])
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	corrupt := bytes.Repeat([]byte{'x'}, len(payload))
	if err := os.WriteFile(path, corrupt, 0o644); err != nil {
		t.Fatal(err)
	}

	source := &countingReader{reader: bytes.NewReader(payload)}
	_, err := store.Publish(context.Background(), digest, source)
	if !errors.Is(err, ErrCorruptArtifact) {
		t.Fatalf("Publish error = %v, want ErrCorruptArtifact", err)
	}
	if source.read != len(payload) {
		t.Fatalf("source bytes read = %d, want %d", source.read, len(payload))
	}
	onDisk, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(onDisk, corrupt) {
		t.Fatal("Publish implicitly repaired or replaced corrupt artifact")
	}

	if _, err := store.Read(context.Background(), digest); !errors.Is(err, ErrCorruptArtifact) {
		t.Fatalf("Read error = %v, want ErrCorruptArtifact", err)
	}
	assertOnlyObjects(t, root, digest)
}

type cancelingReader struct {
	cancel context.CancelFunc
	reader *bytes.Reader
	once   sync.Once
}

func (r *cancelingReader) Read(p []byte) (int, error) {
	n, err := r.reader.Read(p)
	if n > 0 {
		r.once.Do(r.cancel)
	}
	return n, err
}

type failAfterDataReader struct {
	data []byte
	err  error
	done bool
}

func (r *failAfterDataReader) Read(p []byte) (int, error) {
	if r.done {
		return 0, r.err
	}
	r.done = true
	n := copy(p, r.data)
	return n, r.err
}

func TestFailuresAndCancellationCleanCandidates(t *testing.T) {
	t.Run("cancellation during source read", func(t *testing.T) {
		root := t.TempDir()
		payload := bytes.Repeat([]byte("cancel me\n"), 16384)
		digest := digestOf(payload)
		ctx, cancel := context.WithCancel(context.Background())
		source := &cancelingReader{cancel: cancel, reader: bytes.NewReader(payload)}

		_, err := NewStore(root).Publish(ctx, digest, source)
		if !errors.Is(err, context.Canceled) {
			t.Fatalf("error = %v, want context.Canceled", err)
		}
		if _, err := os.Stat(filepath.Join(root, digest[:2], digest[2:])); !errors.Is(err, fs.ErrNotExist) {
			t.Fatalf("final artifact exists after cancellation: %v", err)
		}
		assertOnlyObjects(t, root)
	})

	t.Run("digest mismatch", func(t *testing.T) {
		root := t.TempDir()
		expected := digestOf([]byte("expected"))
		_, err := NewStore(root).Publish(context.Background(), expected, strings.NewReader("different"))
		if !errors.Is(err, ErrDigestMismatch) {
			t.Fatalf("error = %v, want ErrDigestMismatch", err)
		}
		assertOnlyObjects(t, root)
	})

	t.Run("source failure", func(t *testing.T) {
		root := t.TempDir()
		boom := errors.New("source exploded")
		payload := []byte("partial bytes")
		_, err := NewStore(root).Publish(context.Background(), digestOf(payload), &failAfterDataReader{
			data: payload,
			err:  boom,
		})
		if !errors.Is(err, boom) {
			t.Fatalf("error = %v, want source error", err)
		}
		assertOnlyObjects(t, root)
	})
}

func TestFinalPathIsInvisibleUntilComplete(t *testing.T) {
	root := t.TempDir()
	store := NewStore(root)
	payload := bytes.Repeat([]byte("never partial\n"), 8192)
	digest := digestOf(payload)
	started := make(chan struct{})
	release := make(chan struct{})
	done := make(chan error, 1)

	go func() {
		_, err := store.Publish(context.Background(), digest, &pausingReader{
			started: started,
			release: release,
			reader:  bytes.NewReader(payload),
		})
		done <- err
	}()
	select {
	case <-started:
	case <-time.After(5 * time.Second):
		t.Fatal("publisher did not reach source read")
	}

	if data, err := store.Read(context.Background(), digest); !errors.Is(err, fs.ErrNotExist) {
		t.Fatalf("Read while publication paused = (%d bytes, %v), want not found", len(data), err)
	}
	close(release)
	if err := <-done; err != nil {
		t.Fatalf("Publish: %v", err)
	}
	data, err := store.Read(context.Background(), digest)
	if err != nil {
		t.Fatalf("Read after publication: %v", err)
	}
	if !bytes.Equal(data, payload) {
		t.Fatal("Read after publication returned incomplete bytes")
	}
}

func TestDigestValidationAndCanceledRead(t *testing.T) {
	store := NewStore(t.TempDir())
	for _, digest := range []string{
		"",
		strings.Repeat("a", 63),
		strings.Repeat("a", 65),
		strings.Repeat("A", 64),
		strings.Repeat("g", 64),
	} {
		if _, err := store.Read(context.Background(), digest); !errors.Is(err, ErrInvalidDigest) {
			t.Errorf("Read(%q) error = %v, want ErrInvalidDigest", digest, err)
		}
	}

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := store.Read(ctx, strings.Repeat("a", 64)); !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled Read error = %v, want context.Canceled", err)
	}
}

// TestProcessPublishWorker is invoked in a fresh test process by
// TestPublishIsAtomicAcrossProcesses.
func TestProcessPublishWorker(t *testing.T) {
	if os.Getenv("ARTIFACT_PROCESS_WORKER") != "1" {
		t.Skip("helper process only")
	}
	root := os.Getenv("ARTIFACT_PROCESS_ROOT")
	gate := os.Getenv("ARTIFACT_PROCESS_GATE")
	id := os.Getenv("ARTIFACT_PROCESS_ID")
	payload := bytes.Repeat([]byte("cross-process artifact\n"), 4096)
	digest := digestOf(payload)

	source := &fileGateReader{
		reader:  bytes.NewReader(payload),
		ready:   filepath.Join(gate, "ready-"+id),
		release: filepath.Join(gate, "release"),
	}
	result, err := NewStore(root).Publish(context.Background(), digest, source)
	if err != nil {
		t.Fatalf("Publish: %v", err)
	}
	value := "0"
	if result.Created {
		value = "1"
	}
	if err := os.WriteFile(filepath.Join(gate, "result-"+id), []byte(value), 0o600); err != nil {
		t.Fatalf("write result: %v", err)
	}
}

type fileGateReader struct {
	once    sync.Once
	reader  *bytes.Reader
	ready   string
	release string
	gateErr error
}

func (r *fileGateReader) Read(p []byte) (int, error) {
	r.once.Do(func() {
		if err := os.WriteFile(r.ready, []byte("ready"), 0o600); err != nil {
			r.gateErr = err
			return
		}
		deadline := time.Now().Add(10 * time.Second)
		for {
			if _, err := os.Stat(r.release); err == nil {
				return
			} else if !errors.Is(err, fs.ErrNotExist) {
				r.gateErr = err
				return
			}
			if time.Now().After(deadline) {
				r.gateErr = errors.New("timed out waiting for process release")
				return
			}
			time.Sleep(2 * time.Millisecond)
		}
	})
	if r.gateErr != nil {
		return 0, r.gateErr
	}
	return r.reader.Read(p)
}

func TestPublishIsAtomicAcrossProcesses(t *testing.T) {
	const workers = 6
	parent := t.TempDir()
	root := filepath.Join(parent, "objects")
	gate := filepath.Join(parent, "gate")
	if err := os.MkdirAll(gate, 0o755); err != nil {
		t.Fatal(err)
	}

	type child struct {
		cmd    *exec.Cmd
		output bytes.Buffer
	}
	children := make([]child, workers)
	for i := 0; i < workers; i++ {
		cmd := exec.Command(os.Args[0], "-test.run=^TestProcessPublishWorker$", "-test.count=1")
		cmd.Env = append(os.Environ(),
			"ARTIFACT_PROCESS_WORKER=1",
			"ARTIFACT_PROCESS_ROOT="+root,
			"ARTIFACT_PROCESS_GATE="+gate,
			"ARTIFACT_PROCESS_ID="+strconv.Itoa(i),
		)
		children[i].cmd = cmd
		cmd.Stdout = &children[i].output
		cmd.Stderr = &children[i].output
		if err := cmd.Start(); err != nil {
			t.Fatalf("start worker %d: %v", i, err)
		}
	}

	for i := 0; i < workers; i++ {
		waitForFile(t, filepath.Join(gate, "ready-"+strconv.Itoa(i)), 10*time.Second)
	}
	if err := os.WriteFile(filepath.Join(gate, "release"), []byte("go"), 0o600); err != nil {
		t.Fatal(err)
	}
	for i := range children {
		if err := children[i].cmd.Wait(); err != nil {
			t.Fatalf("worker %d: %v\n%s", i, err, children[i].output.String())
		}
	}

	created := 0
	for i := 0; i < workers; i++ {
		data, err := os.ReadFile(filepath.Join(gate, "result-"+strconv.Itoa(i)))
		if err != nil {
			t.Fatalf("read result %d: %v", i, err)
		}
		if string(data) == "1" {
			created++
		} else if string(data) != "0" {
			t.Fatalf("worker %d wrote invalid result %q", i, data)
		}
	}
	if created != 1 {
		t.Fatalf("cross-process Created=true count = %d, want exactly 1", created)
	}

	payload := bytes.Repeat([]byte("cross-process artifact\n"), 4096)
	digest := digestOf(payload)
	got, err := NewStore(root).Read(context.Background(), digest)
	if err != nil {
		t.Fatalf("Read: %v", err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatal("cross-process publication returned wrong bytes")
	}
	assertOnlyObjects(t, root, digest)
}

func waitForFile(t *testing.T, path string, timeout time.Duration) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for {
		if _, err := os.Stat(path); err == nil {
			return
		} else if !errors.Is(err, fs.ErrNotExist) {
			t.Fatalf("stat %s: %v", path, err)
		}
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", path)
		}
		time.Sleep(2 * time.Millisecond)
	}
}

func assertOnlyObjects(t *testing.T, root string, digests ...string) {
	t.Helper()
	allowed := make(map[string]bool, len(digests))
	for _, digest := range digests {
		allowed[filepath.Join(root, digest[:2], digest[2:])] = true
	}
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if errors.Is(err, fs.ErrNotExist) && path == root {
			return nil
		}
		if err != nil {
			return err
		}
		if entry.IsDir() {
			return nil
		}
		if !allowed[path] {
			return fmt.Errorf("unexpected persistent file %q", path)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
}
