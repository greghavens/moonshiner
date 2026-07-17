package semaphore

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// waitFor polls cond with a generous deadline; it exists only to observe
// that a goroutine reached a queue state, never to assert timing.
func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", what)
		}
		time.Sleep(time.Millisecond)
	}
}

func mustNew(t *testing.T, capacity int64) *Semaphore {
	t.Helper()
	s, err := New(capacity)
	if err != nil {
		t.Fatalf("New(%d): %v", capacity, err)
	}
	return s
}

func TestNewValidation(t *testing.T) {
	if _, err := New(0); err == nil {
		t.Fatal("capacity 0 accepted")
	}
	if _, err := New(-4); err == nil {
		t.Fatal("negative capacity accepted")
	}
}

func TestTryAcquireCountsWeights(t *testing.T) {
	s := mustNew(t, 3)
	if !s.TryAcquire(2) {
		t.Fatal("TryAcquire(2) on empty sem = false")
	}
	if s.TryAcquire(2) {
		t.Fatal("TryAcquire(2) with only 1 free = true")
	}
	if !s.TryAcquire(1) {
		t.Fatal("TryAcquire(1) with 1 free = false")
	}
	s.Release(2)
	if !s.TryAcquire(2) {
		t.Fatal("TryAcquire(2) after Release(2) = false")
	}
}

func TestAcquireBlocksUntilRelease(t *testing.T) {
	s := mustNew(t, 1)
	if err := s.Acquire(context.Background(), 1); err != nil {
		t.Fatalf("first Acquire: %v", err)
	}
	done := make(chan error, 1)
	go func() { done <- s.Acquire(context.Background(), 1) }()
	waitFor(t, "waiter to enqueue", func() bool { return s.Waiters() == 1 })
	select {
	case err := <-done:
		t.Fatalf("Acquire returned (%v) while the token was still held", err)
	default:
	}
	s.Release(1)
	if err := <-done; err != nil {
		t.Fatalf("Acquire after Release: %v", err)
	}
}

func TestWaitersServedInArrivalOrder(t *testing.T) {
	s := mustNew(t, 1)
	if err := s.Acquire(context.Background(), 1); err != nil {
		t.Fatalf("seed Acquire: %v", err)
	}
	order := make(chan string, 3)
	var wg sync.WaitGroup
	for i, name := range []string{"first", "second", "third"} {
		wg.Add(1)
		go func(name string) {
			defer wg.Done()
			if err := s.Acquire(context.Background(), 1); err != nil {
				t.Errorf("%s: %v", name, err)
				return
			}
			order <- name
			s.Release(1)
		}(name)
		waitFor(t, name+" to enqueue", func() bool { return s.Waiters() == i+1 })
	}
	s.Release(1)
	wg.Wait()
	close(order)
	var got []string
	for name := range order {
		got = append(got, name)
	}
	want := []string{"first", "second", "third"}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("service order = %v, want strict FIFO %v", got, want)
		}
	}
}

func TestNoBargingPastQueuedWaiter(t *testing.T) {
	s := mustNew(t, 2)
	if err := s.Acquire(context.Background(), 1); err != nil { // 1 of 2 free
		t.Fatalf("seed Acquire: %v", err)
	}
	bigDone := make(chan struct{})
	go func() {
		if err := s.Acquire(context.Background(), 2); err != nil { // must wait
			t.Errorf("big waiter: %v", err)
		}
		close(bigDone)
	}()
	waitFor(t, "big waiter to enqueue", func() bool { return s.Waiters() == 1 })

	// One token is free, but the queue is not empty: TryAcquire must not
	// sneak past the big waiter at the head.
	if s.TryAcquire(1) {
		t.Fatal("TryAcquire barged past a queued waiter")
	}

	lateDone := make(chan struct{})
	go func() {
		if err := s.Acquire(context.Background(), 1); err != nil {
			t.Errorf("late small waiter: %v", err)
		}
		close(lateDone)
	}()
	waitFor(t, "late waiter to enqueue", func() bool { return s.Waiters() == 2 })

	s.Release(1) // both tokens free: the HEAD (big) must win
	<-bigDone
	select {
	case <-lateDone:
		t.Fatal("late small waiter overtook the big waiter at the head of the queue")
	default:
	}
	s.Release(2)
	<-lateDone
	s.Release(1)
}

func TestAcquireHeavierThanCapacityFailsFast(t *testing.T) {
	s := mustNew(t, 2)
	if err := s.Acquire(context.Background(), 3); err == nil {
		t.Fatal("Acquire(n > capacity) must error immediately, it can never succeed")
	}
	if s.TryAcquire(3) {
		t.Fatal("TryAcquire(n > capacity) = true")
	}
	if !s.TryAcquire(2) {
		t.Fatal("the failed oversized requests must not have consumed tokens")
	}
}

func TestAcquireWithCanceledContext(t *testing.T) {
	s := mustNew(t, 1)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := s.Acquire(ctx, 1); !errors.Is(err, context.Canceled) {
		t.Fatalf("Acquire with pre-canceled ctx = %v, want context.Canceled", err)
	}
	if !s.TryAcquire(1) {
		t.Fatal("failed Acquire must not have consumed the token")
	}
}

func TestCancelWhileWaitingReleasesQueueSlot(t *testing.T) {
	s := mustNew(t, 1)
	if err := s.Acquire(context.Background(), 1); err != nil {
		t.Fatalf("seed Acquire: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- s.Acquire(ctx, 1) }()
	waitFor(t, "waiter to enqueue", func() bool { return s.Waiters() == 1 })
	cancel()
	if err := <-done; !errors.Is(err, context.Canceled) {
		t.Fatalf("canceled waiter got %v, want context.Canceled", err)
	}
	waitFor(t, "queue to drain", func() bool { return s.Waiters() == 0 })
	s.Release(1)
	if !s.TryAcquire(1) {
		t.Fatal("canceled waiter still holds a phantom token")
	}
}

func TestCancelingHeadDoesNotStrandSuccessor(t *testing.T) {
	s := mustNew(t, 1)
	if err := s.Acquire(context.Background(), 1); err != nil {
		t.Fatalf("seed Acquire: %v", err)
	}
	headCtx, cancelHead := context.WithCancel(context.Background())
	headDone := make(chan error, 1)
	go func() { headDone <- s.Acquire(headCtx, 1) }()
	waitFor(t, "head waiter", func() bool { return s.Waiters() == 1 })

	succDone := make(chan error, 1)
	go func() { succDone <- s.Acquire(context.Background(), 1) }()
	waitFor(t, "second waiter", func() bool { return s.Waiters() == 2 })

	cancelHead()
	if err := <-headDone; !errors.Is(err, context.Canceled) {
		t.Fatalf("head waiter got %v, want context.Canceled", err)
	}
	select {
	case err := <-succDone:
		t.Fatalf("successor acquired (%v) before any Release", err)
	default:
	}
	s.Release(1)
	if err := <-succDone; err != nil {
		t.Fatalf("successor after head cancel + release: %v", err)
	}
}

func TestReleaseMoreThanHeldPanics(t *testing.T) {
	s := mustNew(t, 2)
	if !s.TryAcquire(1) {
		t.Fatal("TryAcquire(1)")
	}
	defer func() {
		if recover() == nil {
			t.Fatal("Release(2) with only 1 held must panic — it's a bookkeeping bug in the caller")
		}
	}()
	s.Release(2)
}

func TestStressNeverExceedsCapacity(t *testing.T) {
	const capacity = 4
	s := mustNew(t, capacity)
	var inFlight, peak int64
	var wg sync.WaitGroup
	for g := 0; g < 16; g++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < 200; i++ {
				if err := s.Acquire(context.Background(), 1); err != nil {
					t.Errorf("Acquire: %v", err)
					return
				}
				cur := atomic.AddInt64(&inFlight, 1)
				for {
					old := atomic.LoadInt64(&peak)
					if cur <= old || atomic.CompareAndSwapInt64(&peak, old, cur) {
						break
					}
				}
				atomic.AddInt64(&inFlight, -1)
				s.Release(1)
			}
		}()
	}
	wg.Wait()
	if p := atomic.LoadInt64(&peak); p > capacity {
		t.Fatalf("observed %d concurrent holders, capacity is %d", p, capacity)
	}
	if !s.TryAcquire(capacity) {
		t.Fatal("tokens leaked: full capacity not available after all goroutines released")
	}
}
