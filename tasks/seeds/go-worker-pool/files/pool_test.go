package pool

import (
	"sort"
	"testing"
)

func TestProcessAllItems(t *testing.T) {
	items := make([]int, 100)
	for i := range items {
		items[i] = i
	}
	got := Process(items, 8, func(x int) int { return x * 2 })
	if len(got) != len(items) {
		t.Fatalf("got %d results, want %d", len(got), len(items))
	}
	sort.Ints(got)
	for i, v := range got {
		if v != i*2 {
			t.Fatalf("result[%d] = %d, want %d", i, v, i*2)
		}
	}
}

func TestSingleWorker(t *testing.T) {
	got := Process([]int{1, 2, 3}, 1, func(x int) int { return x + 1 })
	if len(got) != 3 {
		t.Fatalf("got %d results (%v), want 3", len(got), got)
	}
	sort.Ints(got)
	want := []int{2, 3, 4}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got %v, want %v", got, want)
		}
	}
}
