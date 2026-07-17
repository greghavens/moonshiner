package ringbuf

import (
	"testing"
)

func mustRing[T any](t *testing.T, capacity int, policy Policy) *Ring[T] {
	t.Helper()
	r, err := New[T](capacity, policy)
	if err != nil {
		t.Fatalf("New(%d, %v): %v", capacity, policy, err)
	}
	return r
}

func drainAll[T any](r *Ring[T]) []T {
	var out []T
	for {
		v, ok := r.Pop()
		if !ok {
			return out
		}
		out = append(out, v)
	}
}

func assertSeq(t *testing.T, got []int, want ...int) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("sequence = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("sequence = %v, want %v", got, want)
		}
	}
}

func TestNewValidation(t *testing.T) {
	if _, err := New[int](0, Reject); err == nil {
		t.Fatal("capacity 0 accepted")
	}
	if _, err := New[int](-2, Overwrite); err == nil {
		t.Fatal("negative capacity accepted")
	}
	if _, err := New[int](4, Policy(99)); err == nil {
		t.Fatal("bogus policy value accepted")
	}
}

func TestFIFOOrder(t *testing.T) {
	r := mustRing[int](t, 4, Reject)
	for _, v := range []int{10, 20, 30} {
		if !r.Push(v) {
			t.Fatalf("Push(%d) rejected below capacity", v)
		}
	}
	if got := r.Len(); got != 3 {
		t.Fatalf("Len = %d, want 3", got)
	}
	if got := r.Cap(); got != 4 {
		t.Fatalf("Cap = %d, want 4", got)
	}
	assertSeq(t, drainAll(r), 10, 20, 30)
}

func TestPopAndPeekOnEmpty(t *testing.T) {
	r := mustRing[string](t, 2, Reject)
	if v, ok := r.Pop(); ok || v != "" {
		t.Fatalf("Pop on empty = (%q, %v), want zero value and false", v, ok)
	}
	if v, ok := r.Peek(); ok || v != "" {
		t.Fatalf("Peek on empty = (%q, %v), want zero value and false", v, ok)
	}
}

func TestPeekDoesNotConsume(t *testing.T) {
	r := mustRing[int](t, 3, Reject)
	r.Push(7)
	r.Push(8)
	for i := 0; i < 3; i++ {
		if v, ok := r.Peek(); !ok || v != 7 {
			t.Fatalf("Peek #%d = (%d, %v), want (7, true) every time", i, v, ok)
		}
	}
	if got := r.Len(); got != 2 {
		t.Fatalf("Len after Peeks = %d, want 2", got)
	}
}

func TestWraparoundKeepsOrder(t *testing.T) {
	r := mustRing[int](t, 3, Reject)
	r.Push(1)
	r.Push(2)
	r.Push(3)
	if v, _ := r.Pop(); v != 1 {
		t.Fatalf("Pop = %d, want 1", v)
	}
	if v, _ := r.Pop(); v != 2 {
		t.Fatalf("Pop = %d, want 2", v)
	}
	// These land in slots freed at the front of the backing storage.
	if !r.Push(4) || !r.Push(5) {
		t.Fatal("pushes into freed slots rejected")
	}
	assertSeq(t, drainAll(r), 3, 4, 5)
}

func TestRejectPolicyRefusesWhenFullAndKeepsContents(t *testing.T) {
	r := mustRing[int](t, 3, Reject)
	r.Push(1)
	r.Push(2)
	r.Push(3)
	if r.Push(4) {
		t.Fatal("Push on a full Reject ring returned true")
	}
	if got := r.Len(); got != 3 {
		t.Fatalf("Len after rejected push = %d, want 3", got)
	}
	snap := r.Snapshot()
	assertSeq(t, snap, 1, 2, 3)

	if v, _ := r.Pop(); v != 1 {
		t.Fatalf("Pop = %d, want 1 (rejected push must not disturb contents)", v)
	}
	if !r.Push(9) {
		t.Fatal("Push after freeing a slot rejected")
	}
	assertSeq(t, drainAll(r), 2, 3, 9)
}

func TestOverwritePolicyDropsOldest(t *testing.T) {
	r := mustRing[int](t, 3, Overwrite)
	for v := 1; v <= 5; v++ {
		if !r.Push(v) {
			t.Fatalf("Push(%d) on Overwrite ring returned false; overwrite always accepts", v)
		}
	}
	if got := r.Len(); got != 3 {
		t.Fatalf("Len = %d, want 3", got)
	}
	assertSeq(t, drainAll(r), 3, 4, 5)
}

func TestOverwriteAcrossManyWraps(t *testing.T) {
	r := mustRing[int](t, 3, Overwrite)
	for v := 1; v <= 10; v++ {
		r.Push(v)
	}
	assertSeq(t, r.Snapshot(), 8, 9, 10)
	if v, ok := r.Peek(); !ok || v != 8 {
		t.Fatalf("Peek = (%d, %v), want (8, true)", v, ok)
	}
}

func TestOverwriteInterleavedWithPops(t *testing.T) {
	r := mustRing[int](t, 3, Overwrite)
	r.Push(1)
	r.Push(2)
	r.Push(3)
	r.Push(4) // drops 1
	if v, _ := r.Pop(); v != 2 {
		t.Fatalf("Pop = %d, want 2 (1 was overwritten)", v)
	}
	r.Push(5)
	r.Push(6) // ring now holds 3,4,5,6 -> capacity 3 -> drops 3
	assertSeq(t, drainAll(r), 4, 5, 6)
}

func TestSnapshotIsACopyInOrder(t *testing.T) {
	r := mustRing[int](t, 4, Reject)
	r.Push(1)
	r.Push(2)
	r.Push(3)
	snap := r.Snapshot()
	assertSeq(t, snap, 1, 2, 3)
	snap[0] = 999 // scribbling on the snapshot must not touch the ring
	if v, _ := r.Peek(); v != 1 {
		t.Fatalf("ring corrupted through snapshot: Peek = %d, want 1", v)
	}
	if empty := mustRing[int](t, 2, Reject).Snapshot(); len(empty) != 0 {
		t.Fatalf("Snapshot of empty ring = %v, want empty", empty)
	}
}

func TestLongInterleavedSequence(t *testing.T) {
	// Steady-state churn: push two, pop one, 200 times. Everything that
	// goes in must come out exactly once, in order.
	r := mustRing[int](t, 5, Reject)
	next := 0
	var got []int
	for i := 0; i < 200; i++ {
		for j := 0; j < 2; j++ {
			if r.Len() < r.Cap() {
				if !r.Push(next) {
					t.Fatalf("Push(%d) rejected with Len %d < Cap %d", next, r.Len(), r.Cap())
				}
				next++
			}
		}
		if v, ok := r.Pop(); ok {
			got = append(got, v)
		}
	}
	got = append(got, drainAll(r)...)
	if len(got) != next {
		t.Fatalf("popped %d values, pushed %d", len(got), next)
	}
	for i, v := range got {
		if v != i {
			t.Fatalf("got[%d] = %d, want %d — order or slot bookkeeping broke during churn", i, v, i)
		}
	}
}

func TestWorksWithStructValues(t *testing.T) {
	type event struct {
		ID   int
		Name string
	}
	r := mustRing[event](t, 2, Overwrite)
	r.Push(event{1, "boot"})
	r.Push(event{2, "ready"})
	r.Push(event{3, "panic"})
	v, ok := r.Pop()
	if !ok || v.ID != 2 || v.Name != "ready" {
		t.Fatalf("Pop = (%+v, %v), want ID 2 (oldest surviving)", v, ok)
	}
}
