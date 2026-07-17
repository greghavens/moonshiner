package ipq

import (
	"errors"
	"fmt"
	"sort"
	"testing"
)

func push(t *testing.T, q *Queue, key string, prio int) {
	t.Helper()
	if err := q.Push(key, prio); err != nil {
		t.Fatalf("Push(%q, %d): unexpected error %v", key, prio, err)
	}
}

func drain(t *testing.T, q *Queue) []string {
	t.Helper()
	var keys []string
	for q.Len() > 0 {
		key, _, err := q.Pop()
		if err != nil {
			t.Fatalf("Pop with Len()=%d: unexpected error %v", q.Len()+1, err)
		}
		keys = append(keys, key)
	}
	return keys
}

func wantOrder(t *testing.T, got, want []string) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("drained %d items %v, want %d items %v", len(got), got, len(want), want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("drain order = %v, want %v (first mismatch at index %d)", got, want, i)
		}
	}
}

func TestEmptyQueue(t *testing.T) {
	q := New()
	if q.Len() != 0 {
		t.Fatalf("Len() of new queue = %d, want 0", q.Len())
	}
	if _, _, err := q.Pop(); !errors.Is(err, ErrEmpty) {
		t.Errorf("Pop on empty queue: error = %v, want ErrEmpty", err)
	}
	if _, _, err := q.Peek(); !errors.Is(err, ErrEmpty) {
		t.Errorf("Peek on empty queue: error = %v, want ErrEmpty", err)
	}
}

func TestPopsInPriorityOrder(t *testing.T) {
	q := New()
	push(t, q, "compact-segments", 40)
	push(t, q, "page-oncall", 5)
	push(t, q, "rotate-logs", 25)
	push(t, q, "flush-metrics", 10)
	push(t, q, "rebuild-index", 55)
	wantOrder(t, drain(t, q), []string{
		"page-oncall", "flush-metrics", "rotate-logs", "compact-segments", "rebuild-index",
	})
	if q.Len() != 0 {
		t.Fatalf("Len() after draining = %d, want 0", q.Len())
	}
}

func TestEqualPrioritiesAreFIFO(t *testing.T) {
	q := New()
	push(t, q, "job-a", 10)
	push(t, q, "job-b", 10)
	push(t, q, "urgent", 1)
	push(t, q, "job-c", 10)
	push(t, q, "job-d", 10)
	wantOrder(t, drain(t, q), []string{"urgent", "job-a", "job-b", "job-c", "job-d"})
}

func TestPeekDoesNotRemove(t *testing.T) {
	q := New()
	push(t, q, "later", 20)
	push(t, q, "sooner", 3)

	for i := 0; i < 3; i++ {
		key, prio, err := q.Peek()
		if err != nil {
			t.Fatalf("Peek: %v", err)
		}
		if key != "sooner" || prio != 3 {
			t.Fatalf("Peek #%d = (%q, %d), want (sooner, 3)", i+1, key, prio)
		}
	}
	if q.Len() != 2 {
		t.Fatalf("Len() after Peeks = %d, want 2", q.Len())
	}
	key, prio, err := q.Pop()
	if err != nil || key != "sooner" || prio != 3 {
		t.Fatalf("Pop = (%q, %d, %v), want (sooner, 3, nil)", key, prio, err)
	}
}

func TestDuplicateKeyRejected(t *testing.T) {
	q := New()
	push(t, q, "ingest-batch", 7)
	if err := q.Push("ingest-batch", 2); !errors.Is(err, ErrDuplicateKey) {
		t.Fatalf("Push of existing key: error = %v, want ErrDuplicateKey", err)
	}
	if q.Len() != 1 {
		t.Fatalf("Len() after rejected Push = %d, want 1", q.Len())
	}
	key, prio, err := q.Pop()
	if err != nil || key != "ingest-batch" || prio != 7 {
		t.Fatalf("Pop = (%q, %d, %v); rejected Push must not alter the stored entry", key, prio, err)
	}
}

func TestUpdatePriorityMovesBothDirections(t *testing.T) {
	q := New()
	push(t, q, "alpha", 10)
	push(t, q, "beta", 20)
	push(t, q, "gamma", 30)

	// Raise gamma to the front, sink alpha to the back.
	if err := q.UpdatePriority("gamma", 1); err != nil {
		t.Fatalf("UpdatePriority(gamma, 1): %v", err)
	}
	if err := q.UpdatePriority("alpha", 99); err != nil {
		t.Fatalf("UpdatePriority(alpha, 99): %v", err)
	}
	key, prio, err := q.Peek()
	if err != nil || key != "gamma" || prio != 1 {
		t.Fatalf("Peek = (%q, %d, %v), want (gamma, 1, nil)", key, prio, err)
	}
	wantOrder(t, drain(t, q), []string{"gamma", "beta", "alpha"})
}

func TestUpdatePriorityKeepsOriginalArrivalForTies(t *testing.T) {
	q := New()
	push(t, q, "first", 5)
	push(t, q, "second", 5)
	push(t, q, "third", 5)

	// Bounce "first" away and back to the same priority: its arrival
	// order among equals must be preserved, not reset.
	if err := q.UpdatePriority("first", 50); err != nil {
		t.Fatalf("UpdatePriority: %v", err)
	}
	if err := q.UpdatePriority("first", 5); err != nil {
		t.Fatalf("UpdatePriority: %v", err)
	}
	wantOrder(t, drain(t, q), []string{"first", "second", "third"})
}

func TestUpdateUnknownKey(t *testing.T) {
	q := New()
	push(t, q, "present", 1)
	if err := q.UpdatePriority("ghost", 9); !errors.Is(err, ErrUnknownKey) {
		t.Errorf("UpdatePriority of missing key: error = %v, want ErrUnknownKey", err)
	}
	if q.Len() != 1 {
		t.Errorf("Len() after failed update = %d, want 1", q.Len())
	}
}

func TestRemove(t *testing.T) {
	q := New()
	push(t, q, "keep-1", 10)
	push(t, q, "drop-me", 20)
	push(t, q, "keep-2", 30)

	if err := q.Remove("drop-me"); err != nil {
		t.Fatalf("Remove: %v", err)
	}
	if q.Len() != 2 {
		t.Fatalf("Len() after Remove = %d, want 2", q.Len())
	}
	if err := q.Remove("drop-me"); !errors.Is(err, ErrUnknownKey) {
		t.Fatalf("Remove of already-removed key: error = %v, want ErrUnknownKey", err)
	}
	wantOrder(t, drain(t, q), []string{"keep-1", "keep-2"})
}

func TestRemoveThenRepushGetsFreshArrivalOrder(t *testing.T) {
	q := New()
	push(t, q, "retry-42", 8)
	push(t, q, "retry-43", 8)
	if err := q.Remove("retry-42"); err != nil {
		t.Fatalf("Remove: %v", err)
	}
	push(t, q, "retry-42", 8) // re-enqueued: now behind retry-43
	wantOrder(t, drain(t, q), []string{"retry-43", "retry-42"})
}

func TestRemoveInteriorKeepsHeapConsistent(t *testing.T) {
	// Removing an interior element is where index bookkeeping usually
	// breaks; drain order afterwards must still be exact.
	q := New()
	for i := 0; i < 30; i++ {
		push(t, q, fmt.Sprintf("task-%02d", i), (i*11)%7)
	}
	for _, victim := range []string{"task-13", "task-05", "task-28", "task-00"} {
		if err := q.Remove(victim); err != nil {
			t.Fatalf("Remove(%q): %v", victim, err)
		}
	}

	type entry struct {
		key           string
		prio, arrival int
	}
	var want []entry
	removed := map[string]bool{"task-13": true, "task-05": true, "task-28": true, "task-00": true}
	for i := 0; i < 30; i++ {
		key := fmt.Sprintf("task-%02d", i)
		if !removed[key] {
			want = append(want, entry{key, (i * 11) % 7, i})
		}
	}
	sort.SliceStable(want, func(a, b int) bool { return want[a].prio < want[b].prio })
	var wantKeys []string
	for _, e := range want {
		wantKeys = append(wantKeys, e.key)
	}
	wantOrder(t, drain(t, q), wantKeys)
}

func TestDeterministicDrainLarge(t *testing.T) {
	q := New()
	const n = 200
	type entry struct {
		key  string
		prio int
	}
	var model []entry
	for i := 0; i < n; i++ {
		key := fmt.Sprintf("item-%03d", i)
		prio := (i * 7) % 13
		push(t, q, key, prio)
		model = append(model, entry{key, prio})
	}
	sort.SliceStable(model, func(a, b int) bool { return model[a].prio < model[b].prio })

	for i, want := range model {
		key, prio, err := q.Pop()
		if err != nil {
			t.Fatalf("Pop #%d: %v", i, err)
		}
		if key != want.key || prio != want.prio {
			t.Fatalf("Pop #%d = (%q, %d), want (%q, %d)", i, key, prio, want.key, want.prio)
		}
	}
	if _, _, err := q.Pop(); !errors.Is(err, ErrEmpty) {
		t.Fatalf("Pop after full drain: error = %v, want ErrEmpty", err)
	}
}
