package intervals

import (
	"reflect"
	"testing"
)

func TestMergeOverlapping(t *testing.T) {
	got := Merge([]Interval{{1, 3}, {2, 6}, {8, 10}})
	want := []Interval{{1, 6}, {8, 10}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestMergeTouching(t *testing.T) {
	got := Merge([]Interval{{1, 3}, {3, 5}})
	want := []Interval{{1, 5}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("touching intervals must merge: got %v, want %v", got, want)
	}
}

func TestMergeUnsortedInput(t *testing.T) {
	got := Merge([]Interval{{8, 10}, {1, 3}, {2, 6}})
	want := []Interval{{1, 6}, {8, 10}}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestInputNotMutated(t *testing.T) {
	in := []Interval{{5, 7}, {1, 2}}
	Merge(in)
	want := []Interval{{5, 7}, {1, 2}}
	if !reflect.DeepEqual(in, want) {
		t.Fatalf("input slice was modified: got %v, want %v", in, want)
	}
}
