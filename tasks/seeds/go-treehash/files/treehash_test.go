package treehash

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

func writeFile(t *testing.T, root, rel string, content []byte) {
	t.Helper()
	p := filepath.Join(root, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, content, 0o644); err != nil {
		t.Fatal(err)
	}
}

func digest(content []byte) string {
	h := sha256.Sum256(content)
	return hex.EncodeToString(h[:])
}

func TestSortedReportWithCorrectDigests(t *testing.T) {
	root := t.TempDir()
	alpha := []byte("alpha\n")
	deep := bytes.Repeat([]byte{0xA5, 0x00, 0x7F}, 341) // 1023 bytes, binary
	beta := []byte("beta\n")
	top := []byte("top-level")
	adot := []byte("dot file sorts before slash")
	writeFile(t, root, "b/two.txt", beta)
	writeFile(t, root, "a/one.txt", alpha)
	writeFile(t, root, "a/zz/deep.bin", deep)
	writeFile(t, root, "top.txt", top)
	writeFile(t, root, "a.txt", adot)

	got, err := HashTree(root, Options{Workers: 4})
	if err != nil {
		t.Fatalf("HashTree: %v", err)
	}
	// Plain byte-wise sort on the relative slash path: '.' < '/' so
	// "a.txt" must come before "a/one.txt".
	want := []Entry{
		{Path: "a.txt", Size: int64(len(adot)), SHA256: digest(adot)},
		{Path: "a/one.txt", Size: int64(len(alpha)), SHA256: digest(alpha)},
		{Path: "a/zz/deep.bin", Size: int64(len(deep)), SHA256: digest(deep)},
		{Path: "b/two.txt", Size: int64(len(beta)), SHA256: digest(beta)},
		{Path: "top.txt", Size: int64(len(top)), SHA256: digest(top)},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("HashTree report mismatch:\n got: %+v\nwant: %+v", got, want)
	}

	again, err := HashTree(root, Options{Workers: 7})
	if err != nil {
		t.Fatalf("HashTree (second run): %v", err)
	}
	if !reflect.DeepEqual(again, want) {
		t.Fatalf("output not stable across runs/worker counts:\n got: %+v\nwant: %+v", again, want)
	}
}

func TestWellKnownDigests(t *testing.T) {
	root := t.TempDir()
	writeFile(t, root, "hello.txt", []byte("hello world\n"))
	writeFile(t, root, "empty.txt", nil)
	got, err := HashTree(root, Options{})
	if err != nil {
		t.Fatalf("HashTree: %v", err)
	}
	byPath := map[string]Entry{}
	for _, e := range got {
		byPath[e.Path] = e
	}
	if e := byPath["hello.txt"]; e.SHA256 != "a948904f2f0f479b8f8197694b30184b0d2ed1c1cd2a1ec0fb85d299a192a447" {
		t.Fatalf("hello.txt digest = %q, wrong", e.SHA256)
	}
	if e := byPath["empty.txt"]; e.SHA256 != "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" || e.Size != 0 {
		t.Fatalf("empty.txt entry = %+v, wrong", e)
	}
}

func TestEmptyTreeAndMissingRoot(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "only", "dirs", "here"), 0o755); err != nil {
		t.Fatal(err)
	}
	got, err := HashTree(root, Options{})
	if err != nil {
		t.Fatalf("empty tree should not error: %v", err)
	}
	if len(got) != 0 {
		t.Fatalf("empty tree should yield no entries, got %+v", got)
	}
	if _, err := HashTree(filepath.Join(root, "does-not-exist"), Options{}); err == nil {
		t.Fatal("missing root must return an error")
	}
}

func TestSizeFiltersInclusive(t *testing.T) {
	root := t.TempDir()
	names := map[int]string{0: "s000.dat", 5: "s005.dat", 10: "s010.dat", 20: "s020.dat", 100: "s100.dat"}
	for n, name := range names {
		writeFile(t, root, name, bytes.Repeat([]byte("z"), n))
	}

	paths := func(opts Options) []string {
		t.Helper()
		got, err := HashTree(root, opts)
		if err != nil {
			t.Fatalf("HashTree(%+v): %v", opts, err)
		}
		var ps []string
		for _, e := range got {
			ps = append(ps, e.Path)
		}
		return ps
	}

	if got := paths(Options{MinSize: 5, MaxSize: 20}); !reflect.DeepEqual(got, []string{"s005.dat", "s010.dat", "s020.dat"}) {
		t.Fatalf("MinSize=5 MaxSize=20 (inclusive bounds) gave %v", got)
	}
	if got := paths(Options{MinSize: 1}); !reflect.DeepEqual(got, []string{"s005.dat", "s010.dat", "s020.dat", "s100.dat"}) {
		t.Fatalf("MinSize=1 should drop only the empty file, gave %v", got)
	}
	if got := paths(Options{}); len(got) != 5 {
		t.Fatalf("MaxSize=0 means no upper bound and MinSize=0 keeps empty files, gave %v", got)
	}
}

func TestSymlinksNeverFollowedNorReported(t *testing.T) {
	outside := t.TempDir()
	writeFile(t, outside, "target.txt", []byte("outside data"))
	writeFile(t, outside, "sub/inner.txt", []byte("inner data"))

	root := t.TempDir()
	writeFile(t, root, "real.txt", []byte("real"))
	if err := os.Symlink(filepath.Join(outside, "target.txt"), filepath.Join(root, "link_file.txt")); err != nil {
		t.Fatalf("symlink: %v", err)
	}
	if err := os.Symlink(filepath.Join(outside, "sub"), filepath.Join(root, "link_dir")); err != nil {
		t.Fatalf("symlink: %v", err)
	}
	if err := os.Symlink(filepath.Join(root, "gone-target"), filepath.Join(root, "broken_link")); err != nil {
		t.Fatalf("symlink: %v", err)
	}

	got, err := HashTree(root, Options{Workers: 2})
	if err != nil {
		t.Fatalf("HashTree with symlinks present: %v", err)
	}
	want := []Entry{{Path: "real.txt", Size: 4, SHA256: digest([]byte("real"))}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("symlinks leaked into the report:\n got: %+v\nwant: %+v", got, want)
	}
}

func TestProgressPairsAndWorkerCap(t *testing.T) {
	root := t.TempDir()
	const n = 40
	for i := 0; i < n; i++ {
		writeFile(t, root, filepath.Join("d", "f"+string(rune('a'+i/26))+string(rune('a'+i%26))+".dat"),
			bytes.Repeat([]byte{byte(i + 1)}, 64+i))
	}
	writeFile(t, root, "tiny.dat", []byte("x")) // filtered out below, must emit no events

	var (
		mu         sync.Mutex
		inFlight   int
		maxFlight  int
		starts     = map[string]int{}
		dones      = map[string]int{}
		outOfOrder []string
	)
	opts := Options{
		Workers: 4,
		MinSize: 16,
		Progress: func(ev Event) {
			if !ev.Done {
				mu.Lock()
				inFlight++
				if inFlight > maxFlight {
					maxFlight = inFlight
				}
				starts[ev.Path]++
				mu.Unlock()
				time.Sleep(3 * time.Millisecond) // widen the hashing window
				return
			}
			mu.Lock()
			if starts[ev.Path] == 0 {
				outOfOrder = append(outOfOrder, ev.Path)
			}
			inFlight--
			dones[ev.Path]++
			mu.Unlock()
		},
	}
	got, err := HashTree(root, opts)
	if err != nil {
		t.Fatalf("HashTree: %v", err)
	}
	if len(got) != n {
		t.Fatalf("got %d entries, want %d", len(got), n)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(outOfOrder) > 0 {
		t.Fatalf("done event before start event for: %v", outOfOrder)
	}
	if len(starts) != n || len(dones) != n {
		t.Fatalf("expected exactly one start+done pair per hashed file: %d starts, %d dones", len(starts), len(dones))
	}
	for p, c := range starts {
		if c != 1 || dones[p] != 1 {
			t.Fatalf("file %s: %d starts, %d dones (want 1/1)", p, c, dones[p])
		}
	}
	if _, ok := starts["tiny.dat"]; ok {
		t.Fatal("filtered-out file must not produce progress events")
	}
	if maxFlight > 4 {
		t.Fatalf("worker cap violated: %d files hashed concurrently with Workers=4", maxFlight)
	}
	if maxFlight < 2 {
		t.Fatalf("no concurrency observed (max in flight %d): files are hashed one at a time", maxFlight)
	}
}

func TestUnreadableFileReturnsError(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("running as root, permissions are not enforced")
	}
	root := t.TempDir()
	writeFile(t, root, "fine.txt", []byte("fine"))
	writeFile(t, root, "locked.txt", []byte("secret"))
	if err := os.Chmod(filepath.Join(root, "locked.txt"), 0o000); err != nil {
		t.Fatal(err)
	}
	_, err := HashTree(root, Options{Workers: 2})
	if err == nil {
		t.Fatal("unreadable file should surface an error")
	}
	if !strings.Contains(err.Error(), "locked.txt") {
		t.Fatalf("error should mention the offending path, got: %v", err)
	}
}
