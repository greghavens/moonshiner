package txnredo

import (
	"errors"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func openDB(t *testing.T, path string, opts *Options) *DB {
	t.Helper()
	db, err := Open(path, opts)
	if err != nil {
		t.Fatalf("Open(%s): %v", path, err)
	}
	return db
}

func logPath(t *testing.T) string {
	t.Helper()
	return filepath.Join(t.TempDir(), "redo.log")
}

func wantGet(t *testing.T, g interface {
	Get(string) ([]byte, bool)
}, key, want string) {
	t.Helper()
	got, ok := g.Get(key)
	if !ok || string(got) != want {
		t.Fatalf("Get(%q) = %q, %v; want %q, true", key, got, ok, want)
	}
}

func wantMissing(t *testing.T, g interface {
	Get(string) ([]byte, bool)
}, key string) {
	t.Helper()
	if got, ok := g.Get(key); ok {
		t.Fatalf("Get(%q) = %q, true; want missing", key, got)
	}
}

func TestCommitAppliesAllKeysAtomically(t *testing.T) {
	db := openDB(t, logPath(t), nil)
	defer db.Close()

	tx := db.Begin()
	if err := tx.Set("user:7:name", []byte("nina")); err != nil {
		t.Fatalf("Set: %v", err)
	}
	if err := tx.Set("user:7:email", []byte("nina@example.com")); err != nil {
		t.Fatalf("Set: %v", err)
	}
	wantMissing(t, db, "user:7:name") // invisible until commit
	if err := tx.Commit(); err != nil {
		t.Fatalf("Commit: %v", err)
	}
	wantGet(t, db, "user:7:name", "nina")
	wantGet(t, db, "user:7:email", "nina@example.com")
	if keys := db.Keys(); len(keys) != 2 || keys[0] != "user:7:email" || keys[1] != "user:7:name" {
		t.Fatalf("Keys = %v, want sorted [user:7:email user:7:name]", keys)
	}
}

func TestReadYourWritesInsideATransaction(t *testing.T) {
	db := openDB(t, logPath(t), nil)
	defer db.Close()

	setup := db.Begin()
	setup.Set("shared", []byte("committed"))
	setup.Set("doomed", []byte("bye"))
	if err := setup.Commit(); err != nil {
		t.Fatalf("Commit: %v", err)
	}

	tx := db.Begin()
	tx.Set("mine", []byte("draft"))
	tx.Delete("doomed")
	wantGet(t, tx, "mine", "draft")
	wantGet(t, tx, "shared", "committed") // committed state reads through
	wantMissing(t, tx, "doomed")          // own delete wins
	tx.Set("mine", []byte("draft-2"))
	wantGet(t, tx, "mine", "draft-2")

	other := db.Begin()
	wantMissing(t, other, "mine") // peers never see uncommitted writes
	wantGet(t, other, "doomed", "bye")
	other.Abort()

	if err := tx.Commit(); err != nil {
		t.Fatalf("Commit: %v", err)
	}
	wantGet(t, db, "mine", "draft-2")
	wantMissing(t, db, "doomed")
}

func TestAbortDiscardsEverything(t *testing.T) {
	var syncs atomic.Int64
	db := openDB(t, logPath(t), &Options{Sync: func() error {
		syncs.Add(1)
		return nil
	}})
	defer db.Close()

	tx := db.Begin()
	tx.Set("ghost", []byte("boo"))
	if err := tx.Abort(); err != nil {
		t.Fatalf("Abort: %v", err)
	}
	wantMissing(t, db, "ghost")
	if n := syncs.Load(); n != 0 {
		t.Fatalf("aborted txn caused %d syncs, want 0", n)
	}
	if err := tx.Commit(); !errors.Is(err, ErrTxDone) {
		t.Fatalf("Commit after Abort = %v, want ErrTxDone", err)
	}
}

func TestFinishedTransactionsRejectEverything(t *testing.T) {
	db := openDB(t, logPath(t), nil)
	defer db.Close()

	tx := db.Begin()
	tx.Set("k", []byte("v"))
	if err := tx.Commit(); err != nil {
		t.Fatalf("Commit: %v", err)
	}
	if err := tx.Set("k", []byte("late")); !errors.Is(err, ErrTxDone) {
		t.Fatalf("Set after Commit = %v, want ErrTxDone", err)
	}
	if err := tx.Delete("k"); !errors.Is(err, ErrTxDone) {
		t.Fatalf("Delete after Commit = %v, want ErrTxDone", err)
	}
	if err := tx.Commit(); !errors.Is(err, ErrTxDone) {
		t.Fatalf("second Commit = %v, want ErrTxDone", err)
	}
	if err := tx.Abort(); !errors.Is(err, ErrTxDone) {
		t.Fatalf("Abort after Commit = %v, want ErrTxDone", err)
	}
	wantGet(t, db, "k", "v")
}

func TestEmptyKeysAndDefensiveCopies(t *testing.T) {
	db := openDB(t, logPath(t), nil)
	defer db.Close()

	tx := db.Begin()
	if err := tx.Set("", []byte("x")); err == nil {
		t.Fatal("Set with an empty key must error")
	}
	buf := []byte("original")
	tx.Set("copied", buf)
	buf[0] = 'X' // caller reuses its buffer
	if err := tx.Commit(); err != nil {
		t.Fatalf("Commit: %v", err)
	}
	wantGet(t, db, "copied", "original")
	got, _ := db.Get("copied")
	got[0] = 'Y' // caller scribbles on the returned slice
	wantGet(t, db, "copied", "original")
}

func TestEachSequentialCommitSyncsExactlyOnce(t *testing.T) {
	var syncs atomic.Int64
	db := openDB(t, logPath(t), &Options{Sync: func() error {
		syncs.Add(1)
		return nil
	}})
	defer db.Close()

	for i, key := range []string{"a", "b", "c"} {
		tx := db.Begin()
		tx.Set(key, []byte{byte('0' + i)})
		if err := tx.Commit(); err != nil {
			t.Fatalf("Commit %q: %v", key, err)
		}
	}
	if n := syncs.Load(); n != 3 {
		t.Fatalf("syncs after 3 sequential commits = %d, want exactly 3", n)
	}
	empty := db.Begin()
	if err := empty.Commit(); err != nil {
		t.Fatalf("empty Commit: %v", err)
	}
	if n := syncs.Load(); n != 3 {
		t.Fatalf("an empty commit must not sync (got %d, want 3)", n)
	}
}

func TestSyncFailureFailsTheCommitCleanly(t *testing.T) {
	path := logPath(t)
	bad := errors.New("write barrier unavailable")
	var fail atomic.Bool
	db := openDB(t, path, &Options{Sync: func() error {
		if fail.Load() {
			return bad
		}
		return nil
	}})

	good := db.Begin()
	good.Set("stable", []byte("yes"))
	if err := good.Commit(); err != nil {
		t.Fatalf("Commit: %v", err)
	}

	fail.Store(true)
	tx := db.Begin()
	tx.Set("shaky", []byte("no"))
	if err := tx.Commit(); !errors.Is(err, bad) {
		t.Fatalf("Commit under failing sync = %v, want the sync error", err)
	}
	wantMissing(t, db, "shaky") // a failed commit applies nothing
	fail.Store(false)

	retry := db.Begin()
	retry.Set("shaky", []byte("second-try"))
	if err := retry.Commit(); err != nil {
		t.Fatalf("Commit retry: %v", err)
	}
	if err := db.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	// the failed batch must not have been acknowledged into the log either
	db2 := openDB(t, path, nil)
	defer db2.Close()
	wantGet(t, db2, "stable", "yes")
	wantGet(t, db2, "shaky", "second-try")
	if keys := db2.Keys(); len(keys) != 2 {
		t.Fatalf("Keys after reopen = %v, want 2 keys", keys)
	}
}

func TestCloseIsIdempotentAndFinal(t *testing.T) {
	db := openDB(t, logPath(t), nil)
	tx := db.Begin()
	tx.Set("k", []byte("v"))
	if err := db.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if err := db.Close(); err != nil {
		t.Fatalf("second Close = %v, want nil", err)
	}
	if err := tx.Commit(); !errors.Is(err, ErrClosed) {
		t.Fatalf("Commit after Close = %v, want ErrClosed", err)
	}
}

// TestGroupCommitSharesOneFlush pins the group-commit contract: while a
// flush is in flight, arriving commits enqueue (observable via Pending);
// when the in-flight flush returns, the whole queue is written and flushed
// with exactly ONE more Sync call, and every queued commit returns only
// after that shared flush.
func TestGroupCommitSharesOneFlush(t *testing.T) {
	path := logPath(t)
	var syncs atomic.Int64
	release := make(chan struct{})
	firstSyncEntered := make(chan struct{})
	db := openDB(t, path, &Options{Sync: func() error {
		if syncs.Add(1) == 1 {
			close(firstSyncEntered)
			<-release
		}
		return nil
	}})

	if p := db.Pending(); p != 0 {
		t.Fatalf("Pending on idle db = %d, want 0", p)
	}

	leaderDone := make(chan error, 1)
	go func() {
		tx := db.Begin()
		tx.Set("lead", []byte("A"))
		leaderDone <- tx.Commit()
	}()
	<-firstSyncEntered // the leader now holds the only flush slot

	var wg sync.WaitGroup
	followerErrs := make(chan error, 3)
	for _, key := range []string{"f1", "f2", "f3"} {
		wg.Add(1)
		go func(key string) {
			defer wg.Done()
			tx := db.Begin()
			tx.Set(key, []byte("v-"+key))
			followerErrs <- tx.Commit()
		}(key)
	}

	deadline := time.Now().Add(10 * time.Second)
	for db.Pending() != 3 {
		if time.Now().After(deadline) {
			t.Fatalf("Pending = %d, want 3 queued commits", db.Pending())
		}
		time.Sleep(time.Millisecond)
	}
	if n := syncs.Load(); n != 1 {
		t.Fatalf("%d syncs while one flush is in flight, want 1", n)
	}
	select {
	case err := <-leaderDone:
		t.Fatalf("leader commit returned before its flush completed (err=%v)", err)
	default:
	}

	close(release)
	if err := <-leaderDone; err != nil {
		t.Fatalf("leader commit: %v", err)
	}
	wg.Wait()
	close(followerErrs)
	for err := range followerErrs {
		if err != nil {
			t.Fatalf("follower commit: %v", err)
		}
	}
	if n := syncs.Load(); n != 2 {
		t.Fatalf("total syncs = %d, want 2 (three queued commits share one flush)", n)
	}
	if p := db.Pending(); p != 0 {
		t.Fatalf("Pending after quiescing = %d, want 0", p)
	}
	for _, key := range []string{"lead", "f1", "f2", "f3"} {
		if _, ok := db.Get(key); !ok {
			t.Fatalf("key %q missing after group commit", key)
		}
	}
	if err := db.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	db2 := openDB(t, path, nil)
	defer db2.Close()
	wantGet(t, db2, "lead", "A")
	wantGet(t, db2, "f1", "v-f1")
	wantGet(t, db2, "f2", "v-f2")
	wantGet(t, db2, "f3", "v-f3")
}
