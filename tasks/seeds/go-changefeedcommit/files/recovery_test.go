package changefeed_test

import (
	"os"
	"path/filepath"
	"testing"

	changefeed "go-changefeedcommit"
)

func commitPair(t *testing.T, store *changefeed.Store, key, value string) {
	t.Helper()
	if err := store.Update(func(tx *changefeed.Tx) error {
		if err := tx.Put(key, []byte(value)); err != nil {
			return err
		}
		return tx.Emit("changes", key, []byte(value))
	}); err != nil {
		t.Fatalf("Update(%q): %v", key, err)
	}
}

func TestTornCommitCannotReplayOutboxWithoutState(t *testing.T) {
	path := filepath.Join(t.TempDir(), "changes.journal")
	store := openStore(t, path, nil)
	commitPair(t, store, "order/11", "confirmed")
	if err := store.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	info, err := os.Stat(path)
	if err != nil {
		t.Fatalf("Stat: %v", err)
	}
	if info.Size() < 3 {
		t.Fatalf("journal unexpectedly short: %d", info.Size())
	}
	if err := os.Truncate(path, info.Size()-2); err != nil {
		t.Fatalf("truncate simulated torn tail: %v", err)
	}

	reopened := openStore(t, path, nil)
	if _, ok := reopened.Get("order/11"); ok {
		t.Fatal("state from torn transaction was replayed")
	}
	if batches := reopened.Pending(10); len(batches) != 0 {
		t.Fatalf("outbox from torn transaction was replayed: %#v", batches)
	}
	commitPair(t, reopened, "order/12", "confirmed")
	batches := reopened.Pending(10)
	if len(batches) != 1 || batches[0].ID != 1 ||
		batches[0].Events[0].Sequence != 1 ||
		batches[0].Events[0].Key != "order/12" {
		t.Fatalf("journal not cleanly reusable after torn record: %#v", batches)
	}
}

func TestCorruptLastCommitDropsItsStateAndWholeBatch(t *testing.T) {
	path := filepath.Join(t.TempDir(), "changes.journal")
	store := openStore(t, path, nil)
	commitPair(t, store, "first", "one")
	commitPair(t, store, "second", "two")
	if err := store.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	file, err := os.OpenFile(path, os.O_RDWR, 0)
	if err != nil {
		t.Fatalf("open journal for corruption: %v", err)
	}
	info, err := file.Stat()
	if err != nil {
		_ = file.Close()
		t.Fatalf("Stat: %v", err)
	}
	last := []byte{0}
	if _, err := file.ReadAt(last, info.Size()-1); err != nil {
		_ = file.Close()
		t.Fatalf("ReadAt: %v", err)
	}
	last[0] ^= 0xff
	if _, err := file.WriteAt(last, info.Size()-1); err != nil {
		_ = file.Close()
		t.Fatalf("WriteAt: %v", err)
	}
	if err := file.Close(); err != nil {
		t.Fatalf("close corrupting handle: %v", err)
	}

	reopened := openStore(t, path, nil)
	if value, ok := reopened.Get("first"); !ok || string(value) != "one" {
		t.Fatalf("valid prefix state = (%q, %v)", value, ok)
	}
	if _, ok := reopened.Get("second"); ok {
		t.Fatal("state from corrupt transaction was replayed")
	}
	batches := reopened.Pending(10)
	if len(batches) != 1 || batches[0].ID != 1 ||
		batches[0].Events[0].Key != "first" {
		t.Fatalf("corrupt transaction leaked a batch: %#v", batches)
	}

	commitPair(t, reopened, "third", "three")
	batches = reopened.Pending(10)
	if len(batches) != 2 || batches[1].ID != 2 ||
		batches[1].Events[0].Sequence != 2 ||
		batches[1].Events[0].Key != "third" {
		t.Fatalf("sequence after corrupt-tail recovery = %#v", batches)
	}
}

func TestTrailingGarbageIsRemovedAndValidPrefixStaysWritable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "changes.journal")
	store := openStore(t, path, nil)
	commitPair(t, store, "alpha", "one")
	if err := store.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	validInfo, err := os.Stat(path)
	if err != nil {
		t.Fatalf("Stat valid journal: %v", err)
	}
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_APPEND, 0)
	if err != nil {
		t.Fatalf("open journal tail: %v", err)
	}
	if _, err := file.Write([]byte("not-a-frame")); err != nil {
		_ = file.Close()
		t.Fatalf("append garbage: %v", err)
	}
	if err := file.Close(); err != nil {
		t.Fatalf("close journal tail: %v", err)
	}

	reopened := openStore(t, path, nil)
	info, err := os.Stat(path)
	if err != nil {
		t.Fatalf("Stat recovered journal: %v", err)
	}
	if info.Size() != validInfo.Size() {
		t.Fatalf("garbage tail not truncated: size %d, want %d", info.Size(), validInfo.Size())
	}
	commitPair(t, reopened, "beta", "two")
	if got := reopened.Pending(10); len(got) != 2 ||
		got[0].Events[0].Key != "alpha" ||
		got[1].Events[0].Key != "beta" {
		t.Fatalf("valid prefix or later append lost: %#v", got)
	}
}
