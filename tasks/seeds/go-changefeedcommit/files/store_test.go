package changefeed_test

import (
	"errors"
	"fmt"
	"path/filepath"
	"reflect"
	"sync/atomic"
	"testing"

	changefeed "go-changefeedcommit"
)

func openStore(t *testing.T, path string, options *changefeed.Options) *changefeed.Store {
	t.Helper()
	store, err := changefeed.Open(path, options)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	t.Cleanup(func() {
		_ = store.Close()
	})
	return store
}

func TestCommitIsOneDurableOrderedBatch(t *testing.T) {
	path := filepath.Join(t.TempDir(), "changes.journal")
	var syncCalls atomic.Int32
	store := openStore(t, path, &changefeed.Options{
		Sync: func() error {
			syncCalls.Add(1)
			return nil
		},
	})

	value := []byte("draft")
	payload := []byte("created")
	var completed *changefeed.Tx
	err := store.Update(func(tx *changefeed.Tx) error {
		completed = tx
		if err := tx.Put("order/7", value); err != nil {
			return err
		}
		value[0] = 'X'
		got, ok, err := tx.Get("order/7")
		if err != nil || !ok || string(got) != "draft" {
			t.Fatalf("read-your-writes = (%q, %v, %v)", got, ok, err)
		}
		got[0] = 'Y'
		again, _, _ := tx.Get("order/7")
		if string(again) != "draft" {
			t.Fatalf("Tx.Get returned aliased bytes: %q", again)
		}
		if err := tx.Emit("orders", "order/7", payload); err != nil {
			return err
		}
		payload[0] = 'X'
		return tx.Emit("audit", "order/7", []byte("indexed"))
	})
	if err != nil {
		t.Fatalf("Update: %v", err)
	}
	if got := syncCalls.Load(); got != 1 {
		t.Fatalf("one transaction used %d durability barriers, want 1", got)
	}

	got, ok := store.Get("order/7")
	if !ok || string(got) != "draft" {
		t.Fatalf("committed state = (%q, %v)", got, ok)
	}
	got[0] = 'Z'
	got, _ = store.Get("order/7")
	if string(got) != "draft" {
		t.Fatalf("Store.Get returned aliased bytes: %q", got)
	}

	batches := store.Pending(1)
	if len(batches) != 1 || batches[0].ID != 1 || len(batches[0].Events) != 2 {
		t.Fatalf("Pending(1) = %#v", batches)
	}
	first, second := batches[0].Events[0], batches[0].Events[1]
	if first.Sequence != 1 || first.BatchID != 1 || first.Index != 0 ||
		first.Topic != "orders" || first.Key != "order/7" ||
		string(first.Payload) != "created" {
		t.Fatalf("first event = %#v", first)
	}
	if second.Sequence != 2 || second.BatchID != 1 || second.Index != 1 ||
		second.Topic != "audit" || string(second.Payload) != "indexed" {
		t.Fatalf("second event = %#v", second)
	}
	batches[0].Events[0].Payload[0] = 'Q'
	if got := store.Pending(1)[0].Events[0].Payload; string(got) != "created" {
		t.Fatalf("Pending returned aliased payload: %q", got)
	}
	if empty := store.Pending(0); empty == nil || len(empty) != 0 {
		t.Fatalf("Pending(0) must be a non-nil empty slice, got %#v", empty)
	}
	if err := completed.Put("late", []byte("write")); !errors.Is(err, changefeed.ErrTxDone) {
		t.Fatalf("completed Tx.Put error = %v, want ErrTxDone", err)
	}
	if _, _, err := completed.Get("order/7"); !errors.Is(err, changefeed.ErrTxDone) {
		t.Fatalf("completed Tx.Get error = %v, want ErrTxDone", err)
	}
}

func TestCallbackRollbackIsInvisibleAndPreservesError(t *testing.T) {
	path := filepath.Join(t.TempDir(), "changes.journal")
	var syncCalls atomic.Int32
	store := openStore(t, path, &changefeed.Options{
		Sync: func() error {
			syncCalls.Add(1)
			return nil
		},
	})

	original := errors.New("validation rejected transaction")
	var completed *changefeed.Tx
	err := store.Update(func(tx *changefeed.Tx) error {
		completed = tx
		if err := tx.Put("account/9", []byte("changed")); err != nil {
			return err
		}
		if err := tx.Emit("accounts", "account/9", []byte("changed")); err != nil {
			return err
		}
		return original
	})
	if err != original {
		t.Fatalf("Update error = %v, want original error object", err)
	}
	if syncCalls.Load() != 0 {
		t.Fatalf("rolled-back callback performed %d durability barriers", syncCalls.Load())
	}
	if _, ok := store.Get("account/9"); ok {
		t.Fatal("rolled-back state became visible")
	}
	if got := store.Pending(10); len(got) != 0 {
		t.Fatalf("rolled-back events became visible: %#v", got)
	}
	if err := completed.Emit("late", "account/9", nil); !errors.Is(err, changefeed.ErrTxDone) {
		t.Fatalf("completed Tx.Emit error = %v, want ErrTxDone", err)
	}

	if err := store.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	reopened := openStore(t, path, nil)
	if _, ok := reopened.Get("account/9"); ok || len(reopened.Pending(10)) != 0 {
		t.Fatal("rolled-back work appeared after restart")
	}
}

func TestConflictRetryUsesFreshAttemptAndNoRejectedOutbox(t *testing.T) {
	path := filepath.Join(t.TempDir(), "changes.journal")
	var syncCalls atomic.Int32
	var callbacks atomic.Int32
	store := openStore(t, path, &changefeed.Options{
		MaxRetries: 1,
		BeforeCommit: func(attempt int) error {
			if attempt == 1 {
				return changefeed.ErrConflict
			}
			return nil
		},
		Sync: func() error {
			syncCalls.Add(1)
			return nil
		},
	})

	err := store.Update(func(tx *changefeed.Tx) error {
		run := callbacks.Add(1)
		value := fmt.Sprintf("attempt-%d", run)
		if err := tx.Put("profile/2", []byte(value)); err != nil {
			return err
		}
		return tx.Emit("profiles", "profile/2", []byte(value))
	})
	if err != nil {
		t.Fatalf("Update: %v", err)
	}
	if callbacks.Load() != 2 {
		t.Fatalf("callback ran %d times, want 2", callbacks.Load())
	}
	if syncCalls.Load() != 1 {
		t.Fatalf("retried update performed %d durable writes, want 1", syncCalls.Load())
	}
	if value, _ := store.Get("profile/2"); string(value) != "attempt-2" {
		t.Fatalf("state = %q, want second attempt", value)
	}

	if err := store.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	reopened := openStore(t, path, nil)
	batches := reopened.Pending(10)
	if len(batches) != 1 || batches[0].ID != 1 ||
		len(batches[0].Events) != 1 ||
		batches[0].Events[0].Sequence != 1 ||
		string(batches[0].Events[0].Payload) != "attempt-2" {
		t.Fatalf("replayed batches = %#v, want only accepted attempt", batches)
	}
}

func TestCommitHookFailureIsAtomicAndUnchanged(t *testing.T) {
	path := filepath.Join(t.TempDir(), "changes.journal")
	original := errors.New("database commit rejected")
	var syncCalls atomic.Int32
	store := openStore(t, path, &changefeed.Options{
		BeforeCommit: func(int) error { return original },
		Sync: func() error {
			syncCalls.Add(1)
			return nil
		},
	})

	err := store.Update(func(tx *changefeed.Tx) error {
		if err := tx.Put("invoice/4", []byte("paid")); err != nil {
			return err
		}
		return tx.Emit("invoices", "invoice/4", []byte("paid"))
	})
	if err != original {
		t.Fatalf("Update error = %v, want original hook error", err)
	}
	if syncCalls.Load() != 0 {
		t.Fatalf("rejected commit performed %d barriers, want 0", syncCalls.Load())
	}
	if err := store.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	reopened := openStore(t, path, nil)
	if _, ok := reopened.Get("invoice/4"); ok {
		t.Fatal("rejected state appeared after restart")
	}
	if got := reopened.Pending(10); len(got) != 0 {
		t.Fatalf("rejected outbox appeared after restart: %#v", got)
	}
}

func TestBarrierFailureRollsBackWholeRecordAndPreservesError(t *testing.T) {
	path := filepath.Join(t.TempDir(), "changes.journal")
	original := errors.New("durability device failed")
	store := openStore(t, path, &changefeed.Options{
		Sync: func() error { return original },
	})

	err := store.Update(func(tx *changefeed.Tx) error {
		if err := tx.Put("shipment/3", []byte("ready")); err != nil {
			return err
		}
		return tx.Emit("shipments", "shipment/3", []byte("ready"))
	})
	if err != original {
		t.Fatalf("Update error = %v, want original sync error", err)
	}
	if _, ok := store.Get("shipment/3"); ok || len(store.Pending(10)) != 0 {
		t.Fatal("failed durable commit became visible")
	}
	if err := store.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	reopened := openStore(t, path, nil)
	if _, ok := reopened.Get("shipment/3"); ok || len(reopened.Pending(10)) != 0 {
		t.Fatal("failed durable commit appeared after restart")
	}
}

func TestBatchOrderLimitAndDurableAcknowledgement(t *testing.T) {
	path := filepath.Join(t.TempDir(), "changes.journal")
	var failAck atomic.Bool
	ackFailure := errors.New("ack sync failed")
	store := openStore(t, path, &changefeed.Options{
		Sync: func() error {
			if failAck.Load() {
				return ackFailure
			}
			return nil
		},
	})

	if err := store.Update(func(tx *changefeed.Tx) error {
		if err := tx.Put("a", []byte("one")); err != nil {
			return err
		}
		if err := tx.Emit("changes", "a", []byte("one")); err != nil {
			return err
		}
		return tx.Emit("audit", "a", []byte("one"))
	}); err != nil {
		t.Fatalf("first Update: %v", err)
	}
	if err := store.Update(func(tx *changefeed.Tx) error {
		if err := tx.Delete("a"); err != nil {
			return err
		}
		if err := tx.Put("b", []byte("two")); err != nil {
			return err
		}
		return tx.Emit("changes", "b", []byte("two"))
	}); err != nil {
		t.Fatalf("second Update: %v", err)
	}
	if got := store.Keys(); !reflect.DeepEqual(got, []string{"b"}) {
		t.Fatalf("Keys = %v, want [b]", got)
	}

	limited := store.Pending(1)
	if len(limited) != 1 || limited[0].ID != 1 || len(limited[0].Events) != 2 {
		t.Fatalf("Pending(1) split or reordered a batch: %#v", limited)
	}
	all := store.Pending(10)
	if len(all) != 2 || all[0].ID != 1 || all[1].ID != 2 ||
		all[1].Events[0].Sequence != 3 {
		t.Fatalf("Pending order = %#v", all)
	}
	if err := store.Ack(2); !errors.Is(err, changefeed.ErrOutOfOrder) {
		t.Fatalf("Ack(2) error = %v, want ErrOutOfOrder", err)
	}

	failAck.Store(true)
	if err := store.Ack(1); err != ackFailure {
		t.Fatalf("failed Ack error = %v, want original sync error", err)
	}
	if got := store.Pending(10); len(got) != 2 {
		t.Fatalf("failed Ack removed a batch: %#v", got)
	}
	failAck.Store(false)
	if err := store.Ack(1); err != nil {
		t.Fatalf("Ack(1): %v", err)
	}
	if err := store.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}

	reopened := openStore(t, path, nil)
	batches := reopened.Pending(10)
	if len(batches) != 1 || batches[0].ID != 2 ||
		batches[0].Events[0].Sequence != 3 {
		t.Fatalf("pending after restart = %#v, want second batch", batches)
	}
	if err := reopened.Close(); err != nil {
		t.Fatalf("Close reopened store: %v", err)
	}
	if err := reopened.Close(); err != nil {
		t.Fatalf("second Close: %v", err)
	}
	if err := reopened.Update(func(*changefeed.Tx) error { return nil }); !errors.Is(err, changefeed.ErrClosed) {
		t.Fatalf("Update after Close error = %v, want ErrClosed", err)
	}
	if err := reopened.Ack(2); !errors.Is(err, changefeed.ErrClosed) {
		t.Fatalf("Ack after Close error = %v, want ErrClosed", err)
	}
}

func TestEmptyUpdateAndValidationDoNotTouchJournal(t *testing.T) {
	path := filepath.Join(t.TempDir(), "changes.journal")
	var hooks, syncs atomic.Int32
	store := openStore(t, path, &changefeed.Options{
		BeforeCommit: func(int) error {
			hooks.Add(1)
			return nil
		},
		Sync: func() error {
			syncs.Add(1)
			return nil
		},
	})
	if err := store.Update(func(*changefeed.Tx) error { return nil }); err != nil {
		t.Fatalf("empty Update: %v", err)
	}
	if hooks.Load() != 0 || syncs.Load() != 0 {
		t.Fatalf("empty update called hook %d times and sync %d times", hooks.Load(), syncs.Load())
	}
	if err := store.Update(func(tx *changefeed.Tx) error {
		if !errors.Is(tx.Put("", nil), changefeed.ErrEmptyKey) {
			t.Fatal("Put accepted an empty key")
		}
		if !errors.Is(tx.Delete(""), changefeed.ErrEmptyKey) {
			t.Fatal("Delete accepted an empty key")
		}
		if !errors.Is(tx.Emit("", "key", nil), changefeed.ErrEmptyTopic) {
			t.Fatal("Emit accepted an empty topic")
		}
		if !errors.Is(tx.Emit("topic", "", nil), changefeed.ErrEmptyKey) {
			t.Fatal("Emit accepted an empty event key")
		}
		return nil
	}); err != nil {
		t.Fatalf("validation callback: %v", err)
	}
	if hooks.Load() != 0 || syncs.Load() != 0 {
		t.Fatalf("validation-only update touched journal")
	}
}
