package kvlog

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// snapshotWAL copies the live WAL bytes to a new path, simulating what is on
// disk at the moment of a power cut (the store is deliberately NOT closed).
func snapshotWAL(t *testing.T, src, dst string) []byte {
	t.Helper()
	b, err := os.ReadFile(src)
	if err != nil {
		t.Fatalf("read live WAL: %v", err)
	}
	if err := os.WriteFile(dst, b, 0o644); err != nil {
		t.Fatal(err)
	}
	return b
}

func TestCrashWithoutCloseLosesNothingAcknowledged(t *testing.T) {
	dir := t.TempDir()
	wal := filepath.Join(dir, "live.wal")

	s := openStore(t, wal)
	defer s.Close()
	mustSet(t, s, "a", "1")
	mustSet(t, s, "b", "2")
	if err := s.Delete("a"); err != nil {
		t.Fatal(err)
	}
	mustSet(t, s, "c", "3")

	crash := filepath.Join(dir, "crash.wal")
	snapshotWAL(t, wal, crash) // no Close before this: every op must already be on disk

	r := openStore(t, crash)
	defer r.Close()
	expectGet(t, r, "a", "", false)
	expectGet(t, r, "b", "2", true)
	expectGet(t, r, "c", "3", true)
	if n := r.Len(); n != 2 {
		t.Fatalf("recovered Len = %d, want 2", n)
	}
}

func TestTornTailNeverSurfacesPartialValue(t *testing.T) {
	dir := t.TempDir()
	wal := filepath.Join(dir, "live.wal")

	s := openStore(t, wal)
	defer s.Close()
	mustSet(t, s, "alpha", "one")
	mustSet(t, s, "beta", "two")
	mustSet(t, s, "torn", "hello-world")

	full, err := os.ReadFile(wal)
	if err != nil {
		t.Fatal(err)
	}

	for _, cut := range []int{1, 3} {
		trimmed := filepath.Join(dir, fmt.Sprintf("torn-%d.wal", cut))
		if err := os.WriteFile(trimmed, full[:len(full)-cut], 0o644); err != nil {
			t.Fatal(err)
		}
		r, err := Open(trimmed)
		if err != nil {
			t.Fatalf("Open on a torn log must recover, not fail: %v (cut=%d)", err, cut)
		}
		expectGet(t, r, "alpha", "one", true)
		expectGet(t, r, "beta", "two", true)
		v, ok := r.Get("torn")
		if ok && v != "hello-world" {
			t.Fatalf("cut=%d: torn record surfaced a corrupted value %q — must be all or nothing", cut, v)
		}
		wantLen := 2
		if ok {
			wantLen = 3
		}
		if n := r.Len(); n != wantLen {
			t.Fatalf("cut=%d: Len = %d, want %d", cut, n, wantLen)
		}
		if err := r.Close(); err != nil {
			t.Fatal(err)
		}
	}
}

func TestRecoveredStoreKeepsAcceptingWrites(t *testing.T) {
	dir := t.TempDir()
	wal := filepath.Join(dir, "live.wal")

	s := openStore(t, wal)
	mustSet(t, s, "alpha", "one")
	mustSet(t, s, "torn", "hello-world")
	full, err := os.ReadFile(wal)
	if err != nil {
		t.Fatal(err)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}

	torn := filepath.Join(dir, "torn.wal")
	if err := os.WriteFile(torn, full[:len(full)-2], 0o644); err != nil {
		t.Fatal(err)
	}

	r := openStore(t, torn)
	mustSet(t, r, "delta", "4") // append onto a recovered log
	mustSet(t, r, "echo", "5")
	if err := r.Close(); err != nil {
		t.Fatal(err)
	}

	r2 := openStore(t, torn)
	defer r2.Close()
	expectGet(t, r2, "alpha", "one", true)
	expectGet(t, r2, "delta", "4", true)
	expectGet(t, r2, "echo", "5", true)
	if v, ok := r2.Get("torn"); ok && v != "hello-world" {
		t.Fatalf("torn key resurfaced corrupted after append+replay: %q", v)
	}
}

func TestCompactionShrinksAndPreservesState(t *testing.T) {
	dir := t.TempDir()
	wal := filepath.Join(dir, "churn.wal")

	s := openStore(t, wal)
	defer s.Close()
	// 10 rounds of overwrites over 20 keys, then delete half: heavy churn,
	// tiny live state.
	for round := 0; round < 10; round++ {
		for k := 0; k < 20; k++ {
			mustSet(t, s, fmt.Sprintf("key-%02d", k), fmt.Sprintf("round-%d-%s", round, strings.Repeat("x", 100)))
		}
	}
	for k := 10; k < 20; k++ {
		if err := s.Delete(fmt.Sprintf("key-%02d", k)); err != nil {
			t.Fatal(err)
		}
	}
	before, err := os.Stat(wal)
	if err != nil {
		t.Fatal(err)
	}

	if err := s.Compact(); err != nil {
		t.Fatalf("Compact: %v", err)
	}
	after, err := os.Stat(wal)
	if err != nil {
		t.Fatal(err)
	}
	if after.Size() >= before.Size()/2 {
		t.Fatalf("compaction barely helped: %d -> %d bytes (want < half)", before.Size(), after.Size())
	}
	if n := s.Len(); n != 10 {
		t.Fatalf("Len after compact = %d, want 10", n)
	}
	for k := 0; k < 10; k++ {
		expectGet(t, s, fmt.Sprintf("key-%02d", k), fmt.Sprintf("round-9-%s", strings.Repeat("x", 100)), true)
	}
	for k := 10; k < 20; k++ {
		expectGet(t, s, fmt.Sprintf("key-%02d", k), "", false)
	}

	// The compacted file alone must reproduce the state (simulated crash
	// right after compaction).
	copied := filepath.Join(dir, "compacted-copy.wal")
	snapshotWAL(t, wal, copied)
	r := openStore(t, copied)
	defer r.Close()
	if n := r.Len(); n != 10 {
		t.Fatalf("Len from compacted file = %d, want 10", n)
	}
	expectGet(t, r, "key-03", fmt.Sprintf("round-9-%s", strings.Repeat("x", 100)), true)
}

func TestWritesAfterCompactionSurviveReopen(t *testing.T) {
	dir := t.TempDir()
	wal := filepath.Join(dir, "post-compact.wal")

	s := openStore(t, wal)
	mustSet(t, s, "keep", "old")
	mustSet(t, s, "drop", "x")
	if err := s.Delete("drop"); err != nil {
		t.Fatal(err)
	}
	if err := s.Compact(); err != nil {
		t.Fatalf("Compact: %v", err)
	}
	mustSet(t, s, "fresh", "post-compact write")
	mustSet(t, s, "keep", "new")
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}

	r := openStore(t, wal)
	defer r.Close()
	expectGet(t, r, "keep", "new", true)
	expectGet(t, r, "fresh", "post-compact write", true)
	expectGet(t, r, "drop", "", false)
	if n := r.Len(); n != 2 {
		t.Fatalf("Len = %d, want 2", n)
	}
}
