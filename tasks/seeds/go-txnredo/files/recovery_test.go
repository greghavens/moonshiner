package txnredo

import (
	"os"
	"testing"
)

// fileSize returns the current size of the redo log on disk.
func fileSize(t *testing.T, path string) int64 {
	t.Helper()
	info, err := os.Stat(path)
	if err != nil {
		t.Fatalf("stat %s: %v", path, err)
	}
	return info.Size()
}

// commitOne opens the db, applies one committed transaction built by fn,
// closes the db again, and returns the resulting log size.
func commitOne(t *testing.T, path string, fn func(tx *Tx)) int64 {
	t.Helper()
	db := openDB(t, path, nil)
	tx := db.Begin()
	fn(tx)
	if err := tx.Commit(); err != nil {
		t.Fatalf("Commit: %v", err)
	}
	if err := db.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	return fileSize(t, path)
}

func TestReopenReplaysTheFullHistory(t *testing.T) {
	path := logPath(t)

	db := openDB(t, path, nil)
	tx1 := db.Begin()
	tx1.Set("inv:widget", []byte("12"))
	tx1.Set("inv:gadget", []byte("3"))
	if err := tx1.Commit(); err != nil {
		t.Fatalf("Commit: %v", err)
	}
	tx2 := db.Begin()
	tx2.Set("inv:widget", []byte("11")) // overwrite
	tx2.Delete("inv:gadget")            // tombstone
	tx2.Set("inv:sprocket", []byte("40"))
	if err := tx2.Commit(); err != nil {
		t.Fatalf("Commit: %v", err)
	}
	if err := db.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	db2 := openDB(t, path, nil)
	defer db2.Close()
	wantGet(t, db2, "inv:widget", "11")
	wantGet(t, db2, "inv:sprocket", "40")
	wantMissing(t, db2, "inv:gadget") // the delete survives restart
	if keys := db2.Keys(); len(keys) != 2 {
		t.Fatalf("Keys after replay = %v, want 2 live keys", keys)
	}
}

func TestTornFinalRecordRollsBackTheWholeTransaction(t *testing.T) {
	path := logPath(t)
	size1 := commitOne(t, path, func(tx *Tx) {
		tx.Set("safe", []byte("landed"))
	})
	size2 := commitOne(t, path, func(tx *Tx) {
		tx.Set("batch:1", []byte("a"))
		tx.Set("batch:2", []byte("b"))
		tx.Set("batch:3", []byte("c"))
	})
	if size2 <= size1 {
		t.Fatalf("log did not grow: %d then %d", size1, size2)
	}

	// Chop the second transaction's record off mid-bytes: a crash between
	// write and flush leaves exactly this shape on disk.
	if err := os.Truncate(path, size2-2); err != nil {
		t.Fatalf("truncate: %v", err)
	}

	db := openDB(t, path, nil)
	wantGet(t, db, "safe", "landed")
	// All-or-nothing: no key from the torn transaction may survive, even
	// though its first ops were fully present in the file.
	wantMissing(t, db, "batch:1")
	wantMissing(t, db, "batch:2")
	wantMissing(t, db, "batch:3")
	if err := db.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if got := fileSize(t, path); got != size1 {
		t.Fatalf("log size after recovery = %d, want %d (torn record removed)", got, size1)
	}

	// The recovered log must remain writable.
	commitOne(t, path, func(tx *Tx) {
		tx.Set("post-crash", []byte("ok"))
	})
	db2 := openDB(t, path, nil)
	defer db2.Close()
	wantGet(t, db2, "safe", "landed")
	wantGet(t, db2, "post-crash", "ok")
	wantMissing(t, db2, "batch:2")
}

func TestCorruptRecordIsDroppedAtomically(t *testing.T) {
	path := logPath(t)
	size1 := commitOne(t, path, func(tx *Tx) {
		tx.Set("keep", []byte("me"))
	})
	commitOne(t, path, func(tx *Tx) {
		tx.Set("bit:1", []byte("x"))
		tx.Set("bit:2", []byte("y"))
	})

	// Flip one byte a few bytes into the second record. Whether that byte
	// sits in framing or payload, verification must reject the record.
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read log: %v", err)
	}
	data[size1+3] ^= 0xFF
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatalf("rewrite log: %v", err)
	}

	db := openDB(t, path, nil)
	defer db.Close()
	wantGet(t, db, "keep", "me")
	wantMissing(t, db, "bit:1")
	wantMissing(t, db, "bit:2")
	if got := fileSize(t, path); got != size1 {
		t.Fatalf("log size after recovery = %d, want %d (corrupt tail removed)", got, size1)
	}
}

func TestTrailingGarbageIsTruncatedOnOpen(t *testing.T) {
	path := logPath(t)
	size := commitOne(t, path, func(tx *Tx) {
		tx.Set("solid", []byte("ground"))
	})

	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatalf("open for append: %v", err)
	}
	junk := make([]byte, 17)
	for i := range junk {
		junk[i] = 0xFF
	}
	if _, err := f.Write(junk); err != nil {
		t.Fatalf("append junk: %v", err)
	}
	if err := f.Close(); err != nil {
		t.Fatalf("close: %v", err)
	}

	db := openDB(t, path, nil)
	defer db.Close()
	wantGet(t, db, "solid", "ground")
	if keys := db.Keys(); len(keys) != 1 {
		t.Fatalf("Keys = %v, want just [solid]", keys)
	}
	if got := fileSize(t, path); got != size {
		t.Fatalf("log size after recovery = %d, want %d (garbage removed)", got, size)
	}
}

func TestWideTransactionIsAllOrNothingUnderTruncation(t *testing.T) {
	path := logPath(t)
	base := commitOne(t, path, func(tx *Tx) {
		tx.Set("anchor", []byte("v")) // something valid before the wide txn
	})
	full := commitOne(t, path, func(tx *Tx) {
		for _, k := range []string{"w0", "w1", "w2", "w3", "w4", "w5", "w6", "w7", "w8", "w9"} {
			tx.Set(k, []byte("payload-"+k))
		}
	})

	// Cut deep into the wide record: most of its ops are intact on disk,
	// but the transaction must still vanish as a unit.
	cut := base + (full-base)*3/4
	if err := os.Truncate(path, cut); err != nil {
		t.Fatalf("truncate: %v", err)
	}

	db := openDB(t, path, nil)
	defer db.Close()
	wantGet(t, db, "anchor", "v")
	for _, k := range []string{"w0", "w1", "w2", "w3", "w4", "w5", "w6", "w7", "w8", "w9"} {
		wantMissing(t, db, k)
	}
	if got := fileSize(t, path); got != base {
		t.Fatalf("log size after recovery = %d, want %d", got, base)
	}
}
