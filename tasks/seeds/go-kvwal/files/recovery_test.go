package kvwal

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func walPath(dir string) string { return filepath.Join(dir, "wal.log") }

func fileSize(t *testing.T, path string) int64 {
	t.Helper()
	fi, err := os.Stat(path)
	if err != nil {
		t.Fatalf("stat %s: %v", path, err)
	}
	return fi.Size()
}

func TestTornTailDroppedOnReopen(t *testing.T) {
	dir := t.TempDir()
	st := openStore(t, dir)
	mustPut(t, st, "alpha", []byte("survives"))
	mustPut(t, st, "omega", []byte("torn away"))
	mustClose(t, st)

	// Simulate a crash mid-append: the last record loses its final bytes.
	size := fileSize(t, walPath(dir))
	if err := os.Truncate(walPath(dir), size-3); err != nil {
		t.Fatalf("truncate: %v", err)
	}

	st2 := openStore(t, dir)
	wantGet(t, st2, "alpha", []byte("survives"))
	wantMissing(t, st2, "omega")
	if n := st2.Len(); n != 1 {
		t.Fatalf("Len after torn-tail recovery = %d, want 1", n)
	}

	// The store must stay writable after the repair.
	mustPut(t, st2, "recovered", []byte("yes"))
	mustClose(t, st2)

	st3 := openStore(t, dir)
	defer st3.Close()
	wantGet(t, st3, "alpha", []byte("survives"))
	wantGet(t, st3, "recovered", []byte("yes"))
	wantMissing(t, st3, "omega")
	if n := st3.Len(); n != 2 {
		t.Fatalf("Len after write-past-repair reopen = %d, want 2", n)
	}
}

func TestGarbageTailTruncatedInOpen(t *testing.T) {
	dir := t.TempDir()
	st := openStore(t, dir)
	mustPut(t, st, "a", []byte("one"))
	mustPut(t, st, "b", []byte("two"))
	mustClose(t, st)
	clean := fileSize(t, walPath(dir))

	f, err := os.OpenFile(walPath(dir), os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatalf("open wal for append: %v", err)
	}
	if _, err := f.Write(bytes.Repeat([]byte{0xFF}, 16)); err != nil {
		t.Fatalf("append garbage: %v", err)
	}
	if err := f.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}

	st2 := openStore(t, dir)
	wantGet(t, st2, "a", []byte("one"))
	wantGet(t, st2, "b", []byte("two"))
	if got := fileSize(t, walPath(dir)); got != clean {
		t.Fatalf("wal size after Open = %d, want %d: Open must truncate the corrupt tail immediately", got, clean)
	}

	mustPut(t, st2, "c", []byte("three"))
	mustClose(t, st2)

	st3 := openStore(t, dir)
	defer st3.Close()
	wantGet(t, st3, "a", []byte("one"))
	wantGet(t, st3, "b", []byte("two"))
	wantGet(t, st3, "c", []byte("three"))
}

func TestCorruptRecordDropsEverythingAfterIt(t *testing.T) {
	dir := t.TempDir()
	st := openStore(t, dir)
	mustPut(t, st, "first", []byte("payload-1"))
	mustPut(t, st, "second", []byte("payload-2"))
	mustPut(t, st, "third", []byte("payload-3"))
	mustClose(t, st)

	// Flip the first payload byte of the very first record
	// (offset 8: after the 4-byte length and 4-byte CRC). Its checksum no
	// longer matches, so replay can trust nothing from there on.
	data, err := os.ReadFile(walPath(dir))
	if err != nil {
		t.Fatalf("read wal: %v", err)
	}
	if len(data) < 9 {
		t.Fatalf("wal only %d bytes, expected framed records", len(data))
	}
	data[8] ^= 0xFF
	if err := os.WriteFile(walPath(dir), data, 0o644); err != nil {
		t.Fatalf("write wal: %v", err)
	}

	st2 := openStore(t, dir)
	if n := st2.Len(); n != 0 {
		t.Fatalf("Len = %d, want 0: a CRC mismatch invalidates that record and all that follow", n)
	}

	mustPut(t, st2, "fresh", []byte("start over"))
	mustClose(t, st2)

	st3 := openStore(t, dir)
	defer st3.Close()
	wantGet(t, st3, "fresh", []byte("start over"))
	if n := st3.Len(); n != 1 {
		t.Fatalf("Len after recovery reopen = %d, want 1", n)
	}
}

func TestCompactShrinksLogAndPreservesState(t *testing.T) {
	dir := t.TempDir()
	st := openStore(t, dir)
	for i := 0; i < 200; i++ {
		mustPut(t, st, "hot", []byte{'r', byte('0' + i/100), byte('0' + i/10%10), byte('0' + i%10)})
	}
	mustPut(t, st, "keep", []byte("payload"))
	mustPut(t, st, "gone", []byte("junk"))
	if err := st.Delete("gone"); err != nil {
		t.Fatalf("Delete: %v", err)
	}

	before := fileSize(t, walPath(dir))
	if err := st.Compact(); err != nil {
		t.Fatalf("Compact: %v", err)
	}
	after := fileSize(t, walPath(dir))
	if after >= before {
		t.Fatalf("compaction did not shrink the log: %d -> %d bytes", before, after)
	}

	wantGet(t, st, "hot", []byte("r199"))
	wantGet(t, st, "keep", []byte("payload"))
	wantMissing(t, st, "gone")

	// The compacted log must still be appendable and replayable.
	mustPut(t, st, "post", []byte("after-compact"))
	mustClose(t, st)

	st2 := openStore(t, dir)
	defer st2.Close()
	wantGet(t, st2, "hot", []byte("r199"))
	wantGet(t, st2, "keep", []byte("payload"))
	wantGet(t, st2, "post", []byte("after-compact"))
	wantMissing(t, st2, "gone")
	if n := st2.Len(); n != 3 {
		t.Fatalf("Len after compact+reopen = %d, want 3", n)
	}
}

func TestCompactDropsTombstones(t *testing.T) {
	dir := t.TempDir()
	st := openStore(t, dir)
	mustPut(t, st, "doomed", []byte("x"))
	if err := st.Delete("doomed"); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	mustPut(t, st, "live", []byte("y"))
	before := fileSize(t, walPath(dir))

	if err := st.Compact(); err != nil {
		t.Fatalf("Compact: %v", err)
	}
	after := fileSize(t, walPath(dir))
	if after >= before {
		t.Fatalf("compacted log still %d bytes (was %d): dead records and tombstones must be dropped", after, before)
	}
	mustClose(t, st)

	st2 := openStore(t, dir)
	defer st2.Close()
	wantMissing(t, st2, "doomed")
	wantGet(t, st2, "live", []byte("y"))
	if n := st2.Len(); n != 1 {
		t.Fatalf("Len = %d, want 1", n)
	}
}
