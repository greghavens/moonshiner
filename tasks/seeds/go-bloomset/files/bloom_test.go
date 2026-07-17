package bloom

import (
	"errors"
	"fmt"
	"testing"
)

func mustNew(t *testing.T, bits, hashes int) *Filter {
	t.Helper()
	f, err := New(bits, hashes)
	if err != nil {
		t.Fatalf("New(%d, %d): unexpected error %v", bits, hashes, err)
	}
	return f
}

func TestNewValidation(t *testing.T) {
	bad := []struct{ bits, hashes int }{
		{0, 4}, {-8, 4}, {1024, 0}, {1024, -1},
	}
	for _, tc := range bad {
		if _, err := New(tc.bits, tc.hashes); err == nil {
			t.Errorf("New(%d, %d) = nil error, want non-nil", tc.bits, tc.hashes)
		}
	}

	f := mustNew(t, 1024, 4)
	if got := f.Bits(); got != 1024 {
		t.Errorf("Bits() = %d, want 1024", got)
	}
	if got := f.Hashes(); got != 4 {
		t.Errorf("Hashes() = %d, want 4", got)
	}
	if got := f.EstimatedFill(); got != 0 {
		t.Errorf("EstimatedFill() of a fresh filter = %v, want 0", got)
	}
}

func TestEmptyFilterContainsNothing(t *testing.T) {
	f := mustNew(t, 2048, 3)
	for i := 0; i < 100; i++ {
		key := fmt.Sprintf("probe-%03d", i)
		if f.MaybeContains(key) {
			t.Errorf("empty filter claims to contain %q", key)
		}
	}
}

func TestNeverFalseNegative(t *testing.T) {
	f := mustNew(t, 4096, 4)
	for i := 0; i < 300; i++ {
		f.Add(fmt.Sprintf("user-%04d", i))
	}
	for i := 0; i < 300; i++ {
		key := fmt.Sprintf("user-%04d", i)
		if !f.MaybeContains(key) {
			t.Errorf("false negative: %q was added but MaybeContains returned false", key)
		}
	}
}

func TestFalsePositivesAreRare(t *testing.T) {
	f := mustNew(t, 16384, 4)
	for i := 0; i < 500; i++ {
		f.Add(fmt.Sprintf("present-%04d", i))
	}
	falsePositives := 0
	for i := 0; i < 500; i++ {
		if f.MaybeContains(fmt.Sprintf("absent-%04d", i)) {
			falsePositives++
		}
	}
	// With m=16384, k=4, n=500 the theoretical rate is well under 1%.
	// Anything above 10% means the hashing is not spreading bits around.
	if falsePositives > 50 {
		t.Errorf("%d/500 absent keys reported as maybe-present; hash dispersion is broken", falsePositives)
	}
}

func TestEstimatedFill(t *testing.T) {
	f := mustNew(t, 1024, 4)

	f.Add("solo")
	fill := f.EstimatedFill()
	if fill <= 0 {
		t.Fatalf("EstimatedFill() = %v after one Add, want > 0", fill)
	}
	if fill > 4.0/1024.0+1e-12 {
		t.Fatalf("EstimatedFill() = %v after one Add with k=4, want <= 4/1024", fill)
	}

	// Re-adding the same key must not set any new bits.
	f.Add("solo")
	f.Add("solo")
	if got := f.EstimatedFill(); got != fill {
		t.Errorf("EstimatedFill() changed from %v to %v after re-adding the same key", fill, got)
	}

	// Fill is monotonically non-decreasing and bounded by 1.
	prev := fill
	for i := 0; i < 200; i++ {
		f.Add(fmt.Sprintf("extra-%03d", i))
		cur := f.EstimatedFill()
		if cur < prev {
			t.Fatalf("EstimatedFill() decreased from %v to %v after an Add", prev, cur)
		}
		if cur > 1 {
			t.Fatalf("EstimatedFill() = %v, want <= 1", cur)
		}
		prev = cur
	}

	// A small, heavily loaded filter should be mostly full.
	small := mustNew(t, 64, 4)
	for i := 0; i < 200; i++ {
		small.Add(fmt.Sprintf("load-%03d", i))
	}
	if got := small.EstimatedFill(); got < 0.8 {
		t.Errorf("EstimatedFill() = %v for 200 keys in 64 bits, want >= 0.8", got)
	}
}

func TestDeterministicAcrossInstancesAndInsertionOrder(t *testing.T) {
	a := mustNew(t, 2048, 5)
	b := mustNew(t, 2048, 5)
	for i := 0; i < 100; i++ {
		a.Add(fmt.Sprintf("item-%03d", i))
	}
	for i := 99; i >= 0; i-- {
		b.Add(fmt.Sprintf("item-%03d", i))
	}
	if fa, fb := a.EstimatedFill(), b.EstimatedFill(); fa != fb {
		t.Errorf("same keys, different fill: %v vs %v — hashing must be deterministic", fa, fb)
	}
	for i := 0; i < 300; i++ {
		key := fmt.Sprintf("item-%03d", i) // 100 present, 200 absent
		if ga, gb := a.MaybeContains(key), b.MaybeContains(key); ga != gb {
			t.Errorf("MaybeContains(%q) disagrees between identically loaded filters: %v vs %v", key, ga, gb)
		}
	}
}

func TestUnion(t *testing.T) {
	a := mustNew(t, 4096, 4)
	b := mustNew(t, 4096, 4)
	for i := 0; i < 150; i++ {
		a.Add(fmt.Sprintf("left-%03d", i))
		b.Add(fmt.Sprintf("right-%03d", i))
	}
	fillA, fillB := a.EstimatedFill(), b.EstimatedFill()

	if err := a.Union(b); err != nil {
		t.Fatalf("Union of same-shape filters: unexpected error %v", err)
	}

	for i := 0; i < 150; i++ {
		if key := fmt.Sprintf("left-%03d", i); !a.MaybeContains(key) {
			t.Errorf("after union, receiver lost its own key %q", key)
		}
		if key := fmt.Sprintf("right-%03d", i); !a.MaybeContains(key) {
			t.Errorf("after union, receiver is missing the other filter's key %q", key)
		}
	}

	got := a.EstimatedFill()
	if got < fillA || got < fillB {
		t.Errorf("union fill %v is below one of the inputs (%v, %v)", got, fillA, fillB)
	}

	// The argument must be left untouched.
	if b.EstimatedFill() != fillB {
		t.Errorf("Union modified its argument: fill went from %v to %v", fillB, b.EstimatedFill())
	}
	if b.MaybeContains("left-000") && b.MaybeContains("left-001") && b.MaybeContains("left-002") {
		t.Errorf("Union appears to have merged bits into its argument")
	}
}

func TestUnionShapeMismatch(t *testing.T) {
	base := mustNew(t, 2048, 4)
	base.Add("keeper")
	fill := base.EstimatedFill()

	otherBits := mustNew(t, 4096, 4)
	otherHashes := mustNew(t, 2048, 5)
	otherBits.Add("stray")
	otherHashes.Add("stray")

	for _, other := range []*Filter{otherBits, otherHashes} {
		if err := base.Union(other); !errors.Is(err, ErrShapeMismatch) {
			t.Errorf("Union with mismatched shape: error = %v, want ErrShapeMismatch", err)
		}
	}

	// A failed union must not have partially applied anything.
	if got := base.EstimatedFill(); got != fill {
		t.Errorf("failed Union changed the receiver: fill %v -> %v", fill, got)
	}
	if !base.MaybeContains("keeper") {
		t.Errorf("failed Union corrupted the receiver's contents")
	}
}
