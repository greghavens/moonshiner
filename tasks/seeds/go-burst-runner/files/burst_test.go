package burst

import (
	"sync"
	"testing"
	"time"
)

func TestFireWaitsForEveryTask(t *testing.T) {
	r := New()
	var mu sync.Mutex
	completed := 0
	for i := 0; i < 8; i++ {
		r.Queue(func() {
			mu.Lock()
			completed++
			mu.Unlock()
		})
	}
	if r.Queued() != 8 {
		t.Fatalf("Queued() = %d, want 8", r.Queued())
	}
	r.Fire()
	mu.Lock()
	got := completed
	mu.Unlock()
	if got != 8 {
		t.Fatalf("Fire returned with %d of 8 tasks completed", got)
	}
}

func TestTasksHeldUntilFire(t *testing.T) {
	r := New()
	ran := make(chan struct{}, 1)
	r.Queue(func() { ran <- struct{}{} })
	select {
	case <-ran:
		t.Fatal("task ran before Fire was called")
	case <-time.After(50 * time.Millisecond):
	}
	r.Fire()
	select {
	case <-ran:
	case <-time.After(2 * time.Second):
		t.Fatal("task never ran after Fire")
	}
}

func TestRecordingsVisibleAfterFire(t *testing.T) {
	r := New()
	var mu sync.Mutex
	latencies := make([]time.Duration, 0, 16)
	for i := 0; i < 16; i++ {
		d := time.Duration(i) * time.Microsecond
		r.Queue(func() {
			mu.Lock()
			latencies = append(latencies, d)
			mu.Unlock()
		})
	}
	r.Fire()
	mu.Lock()
	n := len(latencies)
	mu.Unlock()
	if n != 16 {
		t.Fatalf("histogram has %d samples after Fire, want 16", n)
	}
}
