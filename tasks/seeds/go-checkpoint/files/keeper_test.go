package checkpoint

// Acceptance for the constructor-injected Keeper API. The fakes below
// script time and storage; every semantic pinned here (names, newest
// selection, staleness math, sweep scope) matches what the package-level
// functions did against the real filesystem and wall clock.

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"
)

// ---------------------------------------------------------------- fakes

type fakeClock struct{ now time.Time }

func (c *fakeClock) Now() time.Time          { return c.now }
func (c *fakeClock) advance(d time.Duration) { c.now = c.now.Add(d) }

type memStore struct {
	files   map[string][]byte
	writes  []string
	removed []string
}

func newMemStore() *memStore { return &memStore{files: map[string][]byte{}} }

func (s *memStore) Write(name string, data []byte) error {
	s.writes = append(s.writes, name)
	s.files[name] = append([]byte(nil), data...)
	return nil
}

func (s *memStore) Read(name string) ([]byte, error) {
	data, ok := s.files[name]
	if !ok {
		return nil, errors.New("read " + name + ": not found")
	}
	return data, nil
}

func (s *memStore) List() ([]string, error) {
	names := make([]string, 0, len(s.files))
	for n := range s.files {
		names = append(names, n)
	}
	sort.Strings(names) // oldest-first for a zero-padded name; order must not matter
	return names, nil
}

func (s *memStore) Remove(name string) error {
	if _, ok := s.files[name]; !ok {
		return errors.New("remove " + name + ": not found")
	}
	delete(s.files, name)
	s.removed = append(s.removed, name)
	return nil
}

// The injected seams are interfaces the fakes (and the shipped adapters)
// must satisfy.
var (
	_ Clock = (*fakeClock)(nil)
	_ Clock = SystemClock{}
	_ Store = (*memStore)(nil)
	_ Store = (*OSStore)(nil)
)

const base = int64(1720000000) // an arbitrary 10-digit unix second

// ---------------------------------------------------------------- tests

func TestSaveNamesCheckpointsFromTheInjectedClock(t *testing.T) {
	store := newMemStore()
	clock := &fakeClock{now: time.Unix(base, 0)}
	k := New(store, clock)

	name, err := k.Save("nightly-etl", []byte("cursor=41"))
	if err != nil {
		t.Fatal(err)
	}
	if name != "nightly-etl-1720000000.ckpt" {
		t.Fatalf("Save name = %q, want nightly-etl-1720000000.ckpt", name)
	}
	if got := store.files[name]; !bytes.Equal(got, []byte("cursor=41")) {
		t.Fatalf("stored payload = %q", got)
	}

	clock.advance(time.Hour)
	name2, err := k.Save("nightly-etl", []byte("cursor=90"))
	if err != nil {
		t.Fatal(err)
	}
	if name2 != "nightly-etl-1720003600.ckpt" {
		t.Fatalf("second Save name = %q, want nightly-etl-1720003600.ckpt", name2)
	}
	if want := []string{name, name2}; !equalStrings(store.writes, want) {
		t.Fatalf("writes = %v, want %v", store.writes, want)
	}
}

func TestLatestPicksNewestByEmbeddedTimestampNotListOrder(t *testing.T) {
	store := newMemStore()
	store.files = map[string][]byte{
		"nightly-etl-1720000000.ckpt": []byte("v1"),
		"nightly-etl-1720003600.ckpt": []byte("v2"),
		"nightly-etl-1720007200.ckpt": []byte("v3"),
		"other-job-1720999999.ckpt":   []byte("other"), // newer, different job
		"notes.txt":                   []byte("keep"),
		"orphan.ckpt":                 []byte("junk"),
		"weird-abc.ckpt":              []byte("junk"),
	}
	k := New(store, &fakeClock{now: time.Unix(base+7200, 0)})

	data, at, err := k.Latest("nightly-etl")
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != "v3" {
		t.Fatalf("Latest payload = %q, want v3", data)
	}
	if want := time.Unix(1720007200, 0); !at.Equal(want) {
		t.Fatalf("Latest time = %v, want %v", at, want)
	}

	// hyphenated job names parse from the LAST hyphen
	data, _, err = k.Latest("other-job")
	if err != nil || string(data) != "other" {
		t.Fatalf("Latest(other-job) = %q, %v", data, err)
	}
}

func TestResumeComparesAgeAgainstTheInjectedClock(t *testing.T) {
	store := newMemStore()
	store.files = map[string][]byte{
		"img-import-1720000000.ckpt": []byte("row=5000"),
	}
	clock := &fakeClock{now: time.Unix(base, 0).Add(30 * time.Minute)}
	k := New(store, clock)

	data, ok, err := k.Resume("img-import", time.Hour)
	if err != nil || !ok || string(data) != "row=5000" {
		t.Fatalf("fresh resume = %q, %v, %v", data, ok, err)
	}

	clock.advance(30 * time.Minute) // age is now exactly maxAge: still fresh
	if _, ok, err = k.Resume("img-import", time.Hour); err != nil || !ok {
		t.Fatalf("resume at exactly maxAge should still hit, got ok=%v err=%v", ok, err)
	}

	clock.advance(time.Second) // one past maxAge: start over
	data, ok, err = k.Resume("img-import", time.Hour)
	if err != nil || ok || data != nil {
		t.Fatalf("stale resume = %q, %v, %v; want nil, false, nil", data, ok, err)
	}

	// a job with no checkpoint starts from scratch without an error
	data, ok, err = k.Resume("ghost", time.Hour)
	if err != nil || ok || data != nil {
		t.Fatalf("ghost resume = %q, %v, %v; want nil, false, nil", data, ok, err)
	}
}

func TestSweepStaleRemovesOnlyStrictlyOlderCheckpoints(t *testing.T) {
	store := newMemStore()
	store.files = map[string][]byte{
		"img-import-1719992800.ckpt": []byte("2h old"),
		"img-import-1719996400.ckpt": []byte("exactly 1h old"),
		"img-import-1719998200.ckpt": []byte("30m old"),
		"report-gen-1719989200.ckpt": []byte("3h old"),
		"notes.txt":                  []byte("keep"),
	}
	k := New(store, &fakeClock{now: time.Unix(base, 0)})

	removed, err := k.SweepStale(time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"img-import-1719992800.ckpt", "report-gen-1719989200.ckpt"}
	if !equalStrings(removed, want) {
		t.Fatalf("removed = %v, want %v (ascending)", removed, want)
	}
	gotRemoved := append([]string(nil), store.removed...)
	sort.Strings(gotRemoved)
	if !equalStrings(gotRemoved, want) {
		t.Fatalf("store saw removes %v, want %v", gotRemoved, want)
	}
	for _, survivor := range []string{
		"img-import-1719996400.ckpt", "img-import-1719998200.ckpt", "notes.txt",
	} {
		if _, ok := store.files[survivor]; !ok {
			t.Fatalf("%s should have survived the sweep", survivor)
		}
	}
}

func TestLatestErrorWrapsErrNoCheckpoint(t *testing.T) {
	k := New(newMemStore(), &fakeClock{now: time.Unix(base, 0)})
	_, _, err := k.Latest("ghost")
	if !errors.Is(err, ErrNoCheckpoint) {
		t.Fatalf("err = %v, want errors.Is(..., ErrNoCheckpoint)", err)
	}
	if !strings.Contains(err.Error(), "ghost") {
		t.Fatalf("err %q should name the job", err)
	}
}

func TestOSStoreKeepsTheLegacyOnDiskBehavior(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "spool")
	store, err := NewOSStore(dir) // must create the directory, like Save did
	if err != nil {
		t.Fatal(err)
	}
	clock := &fakeClock{now: time.Unix(base, 0)}
	k := New(store, clock)

	name, err := k.Save("nightly-etl", []byte("cursor=41"))
	if err != nil {
		t.Fatal(err)
	}
	onDisk, err := os.ReadFile(filepath.Join(dir, name))
	if err != nil || string(onDisk) != "cursor=41" {
		t.Fatalf("on-disk payload = %q, %v", onDisk, err)
	}

	clock.advance(time.Hour)
	if _, err := k.Save("nightly-etl", []byte("cursor=90")); err != nil {
		t.Fatal(err)
	}
	// stray content the sweep and lookup must ignore
	if err := os.WriteFile(filepath.Join(dir, "notes.txt"), []byte("keep"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(dir, "archive"), 0o755); err != nil {
		t.Fatal(err)
	}

	data, at, err := k.Latest("nightly-etl")
	if err != nil || string(data) != "cursor=90" {
		t.Fatalf("Latest = %q, %v", data, err)
	}
	if want := time.Unix(base+3600, 0); !at.Equal(want) {
		t.Fatalf("Latest time = %v, want %v", at, want)
	}

	removed, err := k.SweepStale(30 * time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	if want := []string{"nightly-etl-1720000000.ckpt"}; !equalStrings(removed, want) {
		t.Fatalf("removed = %v, want %v", removed, want)
	}
	if _, err := os.Stat(filepath.Join(dir, "nightly-etl-1720000000.ckpt")); !os.IsNotExist(err) {
		t.Fatalf("swept checkpoint should be gone from disk, stat err = %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, "notes.txt")); err != nil {
		t.Fatalf("stray file must survive: %v", err)
	}
}

func TestSystemClockReadsTheWallClock(t *testing.T) {
	got := SystemClock{}.Now()
	if d := time.Since(got); d < -5*time.Second || d > 5*time.Second {
		t.Fatalf("SystemClock.Now() = %v, not close to the wall clock", got)
	}
}

func equalStrings(got, want []string) bool {
	if len(got) != len(want) {
		return false
	}
	for i := range got {
		if got[i] != want[i] {
			return false
		}
	}
	return true
}
