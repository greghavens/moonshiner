package reconcile

import (
	"errors"
	"fmt"
	"reflect"
	"testing"
)

// collector records every flushed batch, keeping the slices it was handed.
type collector struct {
	batches [][]Change
}

func (c *collector) flush(batch []Change) error {
	c.batches = append(c.batches, batch)
	return nil
}

func (c *collector) all() []Change {
	var out []Change
	for _, b := range c.batches {
		out = append(out, b...)
	}
	return out
}

func pairs(kv ...string) []Pair {
	if len(kv)%2 != 0 {
		panic("pairs: odd argument count")
	}
	var out []Pair
	for i := 0; i < len(kv); i += 2 {
		out = append(out, Pair{Key: kv[i], Value: kv[i+1]})
	}
	return out
}

func TestIdenticalStreamsEmitNothing(t *testing.T) {
	var c collector
	before := pairs("alpha", "1", "beta", "2", "gamma", "3")
	after := pairs("alpha", "1", "beta", "2", "gamma", "3")
	if err := Reconcile(before, after, 10, c.flush); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	if len(c.batches) != 0 {
		t.Fatalf("flush was called %d times for identical streams, want 0 (no empty batches)", len(c.batches))
	}
}

func TestBasicDiff(t *testing.T) {
	var c collector
	before := pairs(
		"app.name", "billing",
		"app.port", "8080",
		"db.host", "10.0.0.5",
		"db.pool", "20",
	)
	after := pairs(
		"app.name", "billing",
		"app.port", "9090",
		"cache.ttl", "300",
		"db.host", "10.0.0.5",
	)
	if err := Reconcile(before, after, 100, c.flush); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	want := []Change{
		{Op: OpChanged, Key: "app.port", Old: "8080", New: "9090"},
		{Op: OpAdded, Key: "cache.ttl", Old: "", New: "300"},
		{Op: OpRemoved, Key: "db.pool", Old: "20", New: ""},
	}
	if got := c.all(); !reflect.DeepEqual(got, want) {
		t.Errorf("changes = %#v\nwant      %#v", got, want)
	}
	if len(c.batches) != 1 {
		t.Errorf("flush called %d times, want 1 (three changes fit one batch)", len(c.batches))
	}
}

func TestEmptySides(t *testing.T) {
	var c collector
	if err := Reconcile(nil, pairs("a", "1", "b", "2"), 10, c.flush); err != nil {
		t.Fatalf("Reconcile(nil, ...): %v", err)
	}
	want := []Change{
		{Op: OpAdded, Key: "a", Old: "", New: "1"},
		{Op: OpAdded, Key: "b", Old: "", New: "2"},
	}
	if got := c.all(); !reflect.DeepEqual(got, want) {
		t.Errorf("empty before: changes = %#v, want %#v", got, want)
	}

	c = collector{}
	if err := Reconcile(pairs("a", "1", "b", "2"), nil, 10, c.flush); err != nil {
		t.Fatalf("Reconcile(..., nil): %v", err)
	}
	want = []Change{
		{Op: OpRemoved, Key: "a", Old: "1", New: ""},
		{Op: OpRemoved, Key: "b", Old: "2", New: ""},
	}
	if got := c.all(); !reflect.DeepEqual(got, want) {
		t.Errorf("empty after: changes = %#v, want %#v", got, want)
	}

	c = collector{}
	if err := Reconcile(nil, nil, 10, c.flush); err != nil {
		t.Fatalf("Reconcile(nil, nil): %v", err)
	}
	if len(c.batches) != 0 {
		t.Errorf("both sides empty: flush called %d times, want 0", len(c.batches))
	}
}

func TestBatchSizing(t *testing.T) {
	// 7 changes with batchSize 3 -> flushes of 3, 3, 1.
	var before, after []Pair
	for i := 0; i < 7; i++ {
		key := fmt.Sprintf("row-%02d", i)
		before = append(before, Pair{Key: key, Value: "old"})
		after = append(after, Pair{Key: key, Value: "new"})
	}
	var c collector
	if err := Reconcile(before, after, 3, c.flush); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	var sizes []int
	for _, b := range c.batches {
		sizes = append(sizes, len(b))
	}
	if !reflect.DeepEqual(sizes, []int{3, 3, 1}) {
		t.Errorf("batch sizes = %v, want [3 3 1]", sizes)
	}

	// Batches handed to flush must remain valid after Reconcile returns:
	// reusing one backing array corrupts earlier batches.
	all := c.all()
	if len(all) != 7 {
		t.Fatalf("total changes = %d, want 7", len(all))
	}
	for i, ch := range all {
		wantKey := fmt.Sprintf("row-%02d", i)
		if ch.Key != wantKey || ch.Op != OpChanged {
			t.Errorf("retained change %d = %+v, want OpChanged for %s (batches must not share a reused buffer)", i, ch, wantKey)
		}
	}

	// An exact multiple must not produce a trailing empty flush.
	c = collector{}
	if err := Reconcile(before[:6], after[:6], 3, c.flush); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	sizes = nil
	for _, b := range c.batches {
		sizes = append(sizes, len(b))
	}
	if !reflect.DeepEqual(sizes, []int{3, 3}) {
		t.Errorf("batch sizes = %v, want [3 3] with no empty trailing flush", sizes)
	}
}

func TestInvalidBatchSize(t *testing.T) {
	for _, size := range []int{0, -1} {
		var c collector
		err := Reconcile(pairs("a", "1"), pairs("a", "2"), size, c.flush)
		if err == nil {
			t.Errorf("Reconcile with batchSize %d: error = nil, want non-nil", size)
		}
		if len(c.batches) != 0 {
			t.Errorf("Reconcile with batchSize %d called flush %d times, want 0", size, len(c.batches))
		}
	}
}

func TestFlushErrorStopsEverything(t *testing.T) {
	boom := errors.New("search index rejected the write")
	calls := 0
	flush := func([]Change) error {
		calls++
		if calls == 2 {
			return boom
		}
		return nil
	}
	var before, after []Pair
	for i := 0; i < 9; i++ {
		key := fmt.Sprintf("doc-%02d", i)
		before = append(before, Pair{Key: key, Value: "v1"})
		after = append(after, Pair{Key: key, Value: "v2"})
	}
	err := Reconcile(before, after, 3, flush)
	if !errors.Is(err, boom) {
		t.Fatalf("Reconcile error = %v, want the flush callback's error", err)
	}
	if calls != 2 {
		t.Fatalf("flush called %d times, want exactly 2 (stop immediately on flush failure)", calls)
	}
}

func TestDuplicateKeysRejected(t *testing.T) {
	var c collector
	err := Reconcile(pairs("a", "1", "b", "2", "b", "3"), pairs("a", "1"), 10, c.flush)
	if !errors.Is(err, ErrDuplicateKey) {
		t.Errorf("duplicate in before: error = %v, want ErrDuplicateKey", err)
	}

	err = Reconcile(pairs("a", "1"), pairs("a", "1", "c", "2", "c", "2"), 10, c.flush)
	if !errors.Is(err, ErrDuplicateKey) {
		t.Errorf("duplicate in after: error = %v, want ErrDuplicateKey", err)
	}
}

func TestUnsortedInputRejected(t *testing.T) {
	var c collector
	err := Reconcile(pairs("a", "1", "c", "2", "b", "3"), pairs("a", "1"), 10, c.flush)
	if !errors.Is(err, ErrUnsorted) {
		t.Errorf("unsorted before: error = %v, want ErrUnsorted", err)
	}

	err = Reconcile(pairs("a", "1"), pairs("m", "1", "k", "2"), 10, c.flush)
	if !errors.Is(err, ErrUnsorted) {
		t.Errorf("unsorted after: error = %v, want ErrUnsorted", err)
	}
}

func TestValidationIsStreaming(t *testing.T) {
	// The walk must be single-pass: complete batches produced before the
	// bad element are already flushed; the partial batch in progress is
	// discarded, and nothing after the error is emitted.
	before := pairs("a", "1", "b", "2", "c", "3", "d", "4", "d", "5")
	after := pairs("a", "1", "b", "9", "c", "9", "d", "9")

	var c collector
	err := Reconcile(before, after, 2, c.flush)
	if !errors.Is(err, ErrDuplicateKey) {
		t.Fatalf("error = %v, want ErrDuplicateKey", err)
	}
	if len(c.batches) != 1 {
		t.Fatalf("flush called %d times, want 1 (the full batch preceding the bad element)", len(c.batches))
	}
	want := []Change{
		{Op: OpChanged, Key: "b", Old: "2", New: "9"},
		{Op: OpChanged, Key: "c", Old: "3", New: "9"},
	}
	if !reflect.DeepEqual(c.batches[0], want) {
		t.Errorf("flushed batch = %#v, want %#v", c.batches[0], want)
	}
}

func TestLargeReconcileAppliesCleanly(t *testing.T) {
	// before: keys 0..299 divisible by 2; after: keys 0..299 divisible by
	// 3, with a value bump on multiples of 30.
	var before, after []Pair
	for i := 0; i < 300; i++ {
		key := fmt.Sprintf("k%03d", i)
		if i%2 == 0 {
			before = append(before, Pair{Key: key, Value: "v1"})
		}
		if i%3 == 0 {
			val := "v1"
			if i%30 == 0 {
				val = "v2"
			}
			after = append(after, Pair{Key: key, Value: val})
		}
	}

	var c collector
	if err := Reconcile(before, after, 10, c.flush); err != nil {
		t.Fatalf("Reconcile: %v", err)
	}

	// Changes must arrive in strictly ascending key order across batches.
	all := c.all()
	for i := 1; i < len(all); i++ {
		if all[i-1].Key >= all[i].Key {
			t.Fatalf("changes out of order: %q at %d then %q", all[i-1].Key, i-1, all[i].Key)
		}
	}

	// Applying the changes to `before` must reproduce `after` exactly.
	state := map[string]string{}
	for _, p := range before {
		state[p.Key] = p.Value
	}
	for _, ch := range all {
		switch ch.Op {
		case OpAdded:
			if _, exists := state[ch.Key]; exists {
				t.Fatalf("OpAdded for existing key %q", ch.Key)
			}
			state[ch.Key] = ch.New
		case OpChanged:
			if got, exists := state[ch.Key]; !exists || got != ch.Old {
				t.Fatalf("OpChanged for key %q: Old=%q but state has %q", ch.Key, ch.Old, got)
			}
			state[ch.Key] = ch.New
		case OpRemoved:
			if got, exists := state[ch.Key]; !exists || got != ch.Old {
				t.Fatalf("OpRemoved for key %q: Old=%q but state has %q", ch.Key, ch.Old, got)
			}
			delete(state, ch.Key)
		default:
			t.Fatalf("unknown Op %v for key %q", ch.Op, ch.Key)
		}
	}
	want := map[string]string{}
	for _, p := range after {
		want[p.Key] = p.Value
	}
	if !reflect.DeepEqual(state, want) {
		t.Fatalf("applying emitted changes to `before` did not reproduce `after`: %d keys vs %d", len(state), len(want))
	}
}
