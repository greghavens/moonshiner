package group

import (
	"context"
	"errors"
	"runtime"
	"sync"
	"sync/atomic"
	"testing"
)

func TestEmptyGroupWaitsCleanly(t *testing.T) {
	g, _ := New(context.Background())
	if err := g.Wait(); err != nil {
		t.Fatalf("Wait on empty group = %v, want nil", err)
	}
}

func TestAllTasksRunAndSucceed(t *testing.T) {
	g, _ := New(context.Background())
	var ran int32
	for i := 0; i < 10; i++ {
		g.Go(func(ctx context.Context) error {
			atomic.AddInt32(&ran, 1)
			return nil
		})
	}
	if err := g.Wait(); err != nil {
		t.Fatalf("Wait = %v, want nil", err)
	}
	if ran != 10 {
		t.Fatalf("%d tasks ran, want 10", ran)
	}
}

func TestTasksReceiveTheDerivedContext(t *testing.T) {
	g, ctx := New(context.Background())
	var got context.Context
	g.Go(func(inner context.Context) error {
		got = inner
		return nil
	})
	if err := g.Wait(); err != nil {
		t.Fatalf("Wait: %v", err)
	}
	if got != ctx {
		t.Fatal("task did not receive the context returned by New")
	}
}

func TestFirstErrorCancelsSharedContext(t *testing.T) {
	g, ctx := New(context.Background())
	boom := errors.New("primary failure")

	// This task only finishes if the group cancels the shared ctx after
	// the failing task returns — otherwise the test times out.
	g.Go(func(ctx context.Context) error {
		<-ctx.Done()
		return ctx.Err()
	})
	g.Go(func(ctx context.Context) error {
		return boom
	})

	err := g.Wait()
	if !errors.Is(err, boom) {
		t.Fatalf("Wait = %v, want the task's error (errors.Is)", err)
	}
	if ctx.Err() == nil {
		t.Fatal("shared ctx not canceled after Wait")
	}
}

func TestWaitReturnsTheFirstErrorNotTheLast(t *testing.T) {
	g, _ := New(context.Background())
	errFirst := errors.New("first: db connection refused")
	errLater := errors.New("later: shutdown")

	// The late failure is sequenced strictly after the first one: it
	// waits for the cancellation that the first error triggers.
	g.Go(func(ctx context.Context) error {
		<-ctx.Done()
		return errLater
	})
	g.Go(func(ctx context.Context) error {
		return errFirst
	})

	err := g.Wait()
	if !errors.Is(err, errFirst) {
		t.Fatalf("Wait = %v, want the FIRST error", err)
	}
	if errors.Is(err, errLater) {
		t.Fatalf("Wait = %v; a later error overwrote the first one", err)
	}
}

func TestWaitBlocksUntilStragglersFinish(t *testing.T) {
	g, _ := New(context.Background())
	boom := errors.New("fast failure")
	release := make(chan struct{})
	var stragglerDone int32

	g.Go(func(ctx context.Context) error {
		// Deliberately ignores ctx: simulates a task that can't stop early.
		<-release
		atomic.StoreInt32(&stragglerDone, 1)
		return nil
	})
	g.Go(func(ctx context.Context) error { return boom })

	waitErr := make(chan error, 1)
	go func() { waitErr <- g.Wait() }()
	close(release)
	err := <-waitErr
	if !errors.Is(err, boom) {
		t.Fatalf("Wait = %v, want %v", err, boom)
	}
	if atomic.LoadInt32(&stragglerDone) != 1 {
		t.Fatal("Wait returned before a still-running task finished")
	}
}

func TestContextCanceledAfterSuccessfulWait(t *testing.T) {
	g, ctx := New(context.Background())
	g.Go(func(ctx context.Context) error { return nil })
	if err := g.Wait(); err != nil {
		t.Fatalf("Wait: %v", err)
	}
	select {
	case <-ctx.Done():
	default:
		t.Fatal("derived ctx must be canceled once Wait returns, even on success (resource hygiene)")
	}
}

func TestParentCancellationReachesTasks(t *testing.T) {
	parent, cancel := context.WithCancel(context.Background())
	g, _ := New(parent)
	entered := make(chan struct{})
	g.Go(func(ctx context.Context) error {
		close(entered)
		<-ctx.Done()
		return ctx.Err()
	})
	<-entered
	cancel()
	if err := g.Wait(); !errors.Is(err, context.Canceled) {
		t.Fatalf("Wait after parent cancel = %v, want context.Canceled", err)
	}
}

func TestSetLimitCapsConcurrency(t *testing.T) {
	g, _ := New(context.Background())
	g.SetLimit(3)
	var inFlight, peak, ran int32
	for i := 0; i < 12; i++ {
		g.Go(func(ctx context.Context) error {
			cur := atomic.AddInt32(&inFlight, 1)
			for {
				old := atomic.LoadInt32(&peak)
				if cur <= old || atomic.CompareAndSwapInt32(&peak, old, cur) {
					break
				}
			}
			for j := 0; j < 4; j++ {
				runtime.Gosched()
			}
			atomic.AddInt32(&inFlight, -1)
			atomic.AddInt32(&ran, 1)
			return nil
		})
	}
	if err := g.Wait(); err != nil {
		t.Fatalf("Wait: %v", err)
	}
	if ran != 12 {
		t.Fatalf("%d tasks ran, want 12", ran)
	}
	if p := atomic.LoadInt32(&peak); p > 3 {
		t.Fatalf("observed %d tasks in flight, limit was 3", p)
	}
}

func TestLimitedGoQueuesBehindRunningTask(t *testing.T) {
	g, _ := New(context.Background())
	g.SetLimit(1)

	var mu sync.Mutex
	var events []string
	log := func(ev string) {
		mu.Lock()
		events = append(events, ev)
		mu.Unlock()
	}

	t1Started := make(chan struct{})
	t1Release := make(chan struct{})
	g.Go(func(ctx context.Context) error {
		log("t1-start")
		close(t1Started)
		<-t1Release
		log("t1-end")
		return nil
	})
	<-t1Started

	secondQueued := make(chan struct{})
	go func() {
		g.Go(func(ctx context.Context) error { // must queue: limit is 1
			log("t2-start")
			return nil
		})
		close(secondQueued)
	}()

	close(t1Release)
	<-secondQueued
	if err := g.Wait(); err != nil {
		t.Fatalf("Wait: %v", err)
	}

	mu.Lock()
	defer mu.Unlock()
	want := []string{"t1-start", "t1-end", "t2-start"}
	if len(events) != len(want) {
		t.Fatalf("events = %v, want %v", events, want)
	}
	for i := range want {
		if events[i] != want[i] {
			t.Fatalf("events = %v, want %v — with limit 1, t2 must not start before t1 finished", events, want)
		}
	}
}

func TestSetLimitPanicsOnBadValueOrWhileActive(t *testing.T) {
	g, _ := New(context.Background())
	func() {
		defer func() {
			if recover() == nil {
				t.Error("SetLimit(0) did not panic")
			}
		}()
		g.SetLimit(0)
	}()

	entered := make(chan struct{})
	release := make(chan struct{})
	g.Go(func(ctx context.Context) error {
		close(entered)
		<-release
		return nil
	})
	<-entered
	func() {
		defer func() {
			if recover() == nil {
				t.Error("SetLimit while tasks are running did not panic")
			}
		}()
		g.SetLimit(2)
	}()
	close(release)
	if err := g.Wait(); err != nil {
		t.Fatalf("Wait: %v", err)
	}
}

func TestStressManyFailuresRaceFree(t *testing.T) {
	g, _ := New(context.Background())
	sentinel := errors.New("task failed")
	for i := 0; i < 100; i++ {
		g.Go(func(ctx context.Context) error {
			return sentinel
		})
	}
	if err := g.Wait(); !errors.Is(err, sentinel) {
		t.Fatalf("Wait = %v, want a task error", err)
	}
}
