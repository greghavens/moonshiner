package dlpool_test

import (
	"errors"
	"fmt"
	"sync"
	"testing"
	"time"

	dlpool "go-dlpool"
)

// ---- test doubles ---------------------------------------------------------

type sleepRecorder struct {
	mu sync.Mutex
	ds []time.Duration
}

func (s *sleepRecorder) sleep(d time.Duration) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.ds = append(s.ds, d)
}

func (s *sleepRecorder) recorded() []time.Duration {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]time.Duration(nil), s.ds...)
}

// flaky returns a Fetcher that fails `failures` times per name before
// succeeding with "payload-<name>".
func flaky(failures int) dlpool.Fetcher {
	var mu sync.Mutex
	attempts := map[string]int{}
	return func(name string) ([]byte, error) {
		mu.Lock()
		attempts[name]++
		n := attempts[name]
		mu.Unlock()
		if n <= failures {
			return nil, fmt.Errorf("mirror timeout fetching %s (try %d)", name, n)
		}
		return []byte("payload-" + name), nil
	}
}

// downloadWithin runs Download in the background and fails the test if it
// has not returned after two seconds. Nothing in these tests ever really
// sleeps, so two seconds is an enormous margin: a healthy run finishes in
// well under a millisecond.
func downloadWithin(t *testing.T, m *dlpool.Manager, names []string) map[string]dlpool.Progress {
	t.Helper()
	done := make(chan map[string]dlpool.Progress, 1)
	go func() { done <- m.Download(names) }()
	select {
	case r := <-done:
		return r
	case <-time.After(2 * time.Second):
		t.Fatalf("Download(%v) did not finish; queued files appear stuck", names)
		return nil
	}
}

func durationsEqual(a, b []time.Duration) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// ---- behavior -------------------------------------------------------------

func TestRetryPacingRestartsPerFile(t *testing.T) {
	rec := &sleepRecorder{}
	m := dlpool.New(flaky(2), dlpool.Options{
		BatchSize: 1, // one file at a time: the recorded pauses are strictly ordered
		Attempts:  3,
		BaseDelay: 100 * time.Millisecond,
		Sleep:     rec.sleep,
	})
	r := downloadWithin(t, m, []string{"pkg-a", "pkg-b"})

	for _, name := range []string{"pkg-a", "pkg-b"} {
		want := dlpool.Progress{Status: "done", Bytes: len("payload-" + name), Attempts: 3}
		if r[name] != want {
			t.Errorf("report[%q] = %+v, want %+v", name, r[name], want)
		}
	}
	want := []time.Duration{
		100 * time.Millisecond, 200 * time.Millisecond, // pkg-a's two retries
		100 * time.Millisecond, 200 * time.Millisecond, // pkg-b's two retries
	}
	if got := rec.recorded(); !durationsEqual(got, want) {
		t.Errorf("retry pauses were %v, want %v (every file backs off from BaseDelay on its own)", got, want)
	}
}

func TestBatchLimitAndCompletion(t *testing.T) {
	var mu sync.Mutex
	inFlight, maxInFlight := 0, 0
	counts := map[string]int{}
	fetch := func(name string) ([]byte, error) {
		mu.Lock()
		inFlight++
		if inFlight > maxInFlight {
			maxInFlight = inFlight
		}
		counts[name]++
		mu.Unlock()
		data := []byte(name)
		mu.Lock()
		inFlight--
		mu.Unlock()
		return data, nil
	}
	m := dlpool.New(fetch, dlpool.Options{
		BatchSize: 2,
		Attempts:  3,
		BaseDelay: time.Millisecond,
		Sleep:     func(time.Duration) {},
	})
	names := []string{"f1", "f2", "f3", "f4", "f5"}
	r := downloadWithin(t, m, names)

	if len(r) != len(names) {
		t.Fatalf("report covers %d files, want %d: %v", len(r), len(names), r)
	}
	for _, name := range names {
		want := dlpool.Progress{Status: "done", Bytes: len(name), Attempts: 1}
		if r[name] != want {
			t.Errorf("report[%q] = %+v, want %+v", name, r[name], want)
		}
	}
	mu.Lock()
	defer mu.Unlock()
	for _, name := range names {
		if counts[name] != 1 {
			t.Errorf("file %q was fetched %d times, want exactly once", name, counts[name])
		}
	}
	if maxInFlight > 2 {
		t.Errorf("saw %d fetches in flight at once; the batch size is 2", maxInFlight)
	}
}

func TestStatusSnapshotDuringRun(t *testing.T) {
	started := make(chan struct{})
	gate := make(chan struct{})
	fetch := func(name string) ([]byte, error) {
		close(started)
		<-gate
		return []byte("hello"), nil
	}
	m := dlpool.New(fetch, dlpool.Options{
		BatchSize: 1,
		Attempts:  1,
		BaseDelay: time.Millisecond,
		Sleep:     func(time.Duration) {},
	})
	done := make(chan map[string]dlpool.Progress, 1)
	go func() { done <- m.Download([]string{"alpha"}) }()

	<-started
	close(gate)
	// The dashboard polls for a snapshot while the download is completing.
	snap := m.Report()
	for name := range snap {
		if name != "alpha" {
			t.Errorf("snapshot mentions unexpected file %q", name)
		}
	}

	select {
	case r := <-done:
		want := dlpool.Progress{Status: "done", Bytes: 5, Attempts: 1}
		if r["alpha"] != want {
			t.Errorf("final report = %+v, want %+v", r["alpha"], want)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Download did not finish after the fetch was released")
	}
}

func TestExhaustedRetriesAreReported(t *testing.T) {
	fetch := func(name string) ([]byte, error) {
		if name == "gone" {
			return nil, errors.New("mirror: 503 service unavailable")
		}
		return []byte("payload-" + name), nil
	}
	rec := &sleepRecorder{}
	m := dlpool.New(fetch, dlpool.Options{
		BatchSize: 4,
		Attempts:  2,
		BaseDelay: 50 * time.Millisecond,
		Sleep:     rec.sleep,
	})
	r := downloadWithin(t, m, []string{"ok", "gone"})

	wantGone := dlpool.Progress{Status: "failed", Attempts: 2, Err: "mirror: 503 service unavailable"}
	if r["gone"] != wantGone {
		t.Errorf("report[%q] = %+v, want %+v", "gone", r["gone"], wantGone)
	}
	wantOK := dlpool.Progress{Status: "done", Bytes: len("payload-ok"), Attempts: 1}
	if r["ok"] != wantOK {
		t.Errorf("report[%q] = %+v, want %+v", "ok", r["ok"], wantOK)
	}
}

func TestEmptyQueue(t *testing.T) {
	m := dlpool.New(
		func(string) ([]byte, error) { return nil, nil },
		dlpool.Options{BatchSize: 3, Sleep: func(time.Duration) {}},
	)
	r := downloadWithin(t, m, nil)
	if len(r) != 0 {
		t.Errorf("an empty queue produced a non-empty report: %v", r)
	}
}
