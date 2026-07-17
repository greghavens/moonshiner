package sortid

import (
	"sync"
	"testing"
)

// Pins today's counter-ID behavior. All of this must keep passing.

func TestNextIsSequentialAndZeroPadded(t *testing.T) {
	g := New("job")
	want := []string{"job-000001", "job-000002", "job-000003"}
	for i, w := range want {
		if got := g.Next(); got != w {
			t.Fatalf("Next() call %d = %q, want %q", i+1, got, w)
		}
	}
}

func TestGeneratorsAreIndependent(t *testing.T) {
	a := New("blob")
	b := New("job")
	a.Next()
	a.Next()
	if got := b.Next(); got != "job-000001" {
		t.Fatalf("fresh generator Next() = %q, want %q", got, "job-000001")
	}
	if a.Issued() != 2 || b.Issued() != 1 {
		t.Fatalf("Issued() = %d/%d, want 2/1", a.Issued(), b.Issued())
	}
}

func TestNextIsSafeForConcurrentUse(t *testing.T) {
	g := New("job")
	const workers, per = 8, 50
	var mu sync.Mutex
	seen := make(map[string]bool)
	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < per; i++ {
				id := g.Next()
				mu.Lock()
				seen[id] = true
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	if len(seen) != workers*per {
		t.Fatalf("got %d distinct IDs, want %d", len(seen), workers*per)
	}
	if g.Issued() != workers*per {
		t.Fatalf("Issued() = %d, want %d", g.Issued(), workers*per)
	}
}

func TestHasPrefix(t *testing.T) {
	if !HasPrefix("job-000004", "job") {
		t.Fatal(`HasPrefix("job-000004", "job") = false, want true`)
	}
	if HasPrefix("jobx-000004", "job") {
		t.Fatal(`HasPrefix("jobx-000004", "job") = true, want false`)
	}
	if HasPrefix("job", "job") {
		t.Fatal(`HasPrefix("job", "job") = true, want false`)
	}
}
