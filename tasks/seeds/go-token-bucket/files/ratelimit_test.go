package ratelimit

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// fakeClock is a deterministic Clock for tests. Advance moves time forward
// and fires every timer whose deadline has been reached. Each call to After
// is recorded on the created channel so tests can synchronize with waiters.
type fakeClock struct {
	mu      sync.Mutex
	now     time.Time
	timers  []fakeTimer
	created chan time.Duration
	afters  int32
}

type fakeTimer struct {
	deadline time.Time
	ch       chan time.Time
}

func newFakeClock() *fakeClock {
	return &fakeClock{
		now:     time.Date(2026, 3, 1, 9, 0, 0, 0, time.UTC),
		created: make(chan time.Duration, 16),
	}
}

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *fakeClock) After(d time.Duration) <-chan time.Time {
	c.mu.Lock()
	ch := make(chan time.Time, 1)
	c.timers = append(c.timers, fakeTimer{deadline: c.now.Add(d), ch: ch})
	c.mu.Unlock()
	atomic.AddInt32(&c.afters, 1)
	c.created <- d
	return ch
}

func (c *fakeClock) Advance(d time.Duration) {
	c.mu.Lock()
	c.now = c.now.Add(d)
	var pending []fakeTimer
	for _, t := range c.timers {
		if !t.deadline.After(c.now) {
			t.ch <- c.now
		} else {
			pending = append(pending, t)
		}
	}
	c.timers = pending
	c.mu.Unlock()
}

func (c *fakeClock) afterCalls() int { return int(atomic.LoadInt32(&c.afters)) }

func mustBucket(t *testing.T, capacity int, refill time.Duration, clock Clock) *Bucket {
	t.Helper()
	b, err := New(capacity, refill, clock)
	if err != nil {
		t.Fatalf("New(%d, %v): %v", capacity, refill, err)
	}
	return b
}

func drain(t *testing.T, b *Bucket, n int) {
	t.Helper()
	for i := 0; i < n; i++ {
		if !b.Allow() {
			t.Fatalf("drain: Allow() call %d/%d returned false", i+1, n)
		}
	}
}

func TestNewValidation(t *testing.T) {
	clock := newFakeClock()
	if _, err := New(0, time.Second, clock); err == nil {
		t.Fatal("capacity 0 accepted")
	}
	if _, err := New(-3, time.Second, clock); err == nil {
		t.Fatal("negative capacity accepted")
	}
	if _, err := New(5, 0, clock); err == nil {
		t.Fatal("zero refill interval accepted")
	}
	if _, err := New(5, -time.Millisecond, clock); err == nil {
		t.Fatal("negative refill interval accepted")
	}
	if _, err := New(5, time.Second, nil); err == nil {
		t.Fatal("nil clock accepted")
	}
}

func TestBucketStartsFull(t *testing.T) {
	b := mustBucket(t, 3, time.Second, newFakeClock())
	for i := 0; i < 3; i++ {
		if !b.Allow() {
			t.Fatalf("Allow() #%d = false, want true (bucket should start full)", i+1)
		}
	}
	if b.Allow() {
		t.Fatal("Allow() succeeded on an empty bucket with no time elapsed")
	}
}

func TestRefillOneTokenPerInterval(t *testing.T) {
	clock := newFakeClock()
	b := mustBucket(t, 5, 100*time.Millisecond, clock)
	drain(t, b, 5)

	clock.Advance(100 * time.Millisecond)
	if !b.Allow() {
		t.Fatal("one full interval elapsed: want one token back")
	}
	if b.Allow() {
		t.Fatal("only one interval elapsed but two tokens were granted")
	}

	clock.Advance(300 * time.Millisecond)
	for i := 0; i < 3; i++ {
		if !b.Allow() {
			t.Fatalf("three intervals elapsed: token %d/3 missing", i+1)
		}
	}
	if b.Allow() {
		t.Fatal("three intervals elapsed but a fourth token was granted")
	}
}

func TestFractionalProgressCarriesOver(t *testing.T) {
	clock := newFakeClock()
	b := mustBucket(t, 2, 100*time.Millisecond, clock)
	drain(t, b, 2)

	clock.Advance(60 * time.Millisecond)
	if b.Allow() {
		t.Fatal("token granted after only 60% of the refill interval")
	}
	// The failed Allow above must not reset accrued progress.
	clock.Advance(40 * time.Millisecond)
	if !b.Allow() {
		t.Fatal("60ms + 40ms should accrue one full token; partial progress was lost")
	}
}

func TestRefillNeverExceedsCapacity(t *testing.T) {
	clock := newFakeClock()
	b := mustBucket(t, 2, 10*time.Millisecond, clock)
	drain(t, b, 2)

	clock.Advance(time.Hour)
	drain(t, b, 2)
	if b.Allow() {
		t.Fatal("bucket refilled beyond its capacity")
	}
}

func TestIdleFullBucketRefillsPromptlyAfterUse(t *testing.T) {
	clock := newFakeClock()
	b := mustBucket(t, 1, 100*time.Millisecond, clock)

	// Sitting full for a long time must not bank hidden credit,
	// and must not push the next refill further away either.
	clock.Advance(time.Hour)
	drain(t, b, 1)
	if b.Allow() {
		t.Fatal("idle time banked extra tokens beyond capacity")
	}
	clock.Advance(100 * time.Millisecond)
	if !b.Allow() {
		t.Fatal("token consumed at T should regenerate by T+refill, even after a long idle stretch")
	}
}

func TestAllowN(t *testing.T) {
	clock := newFakeClock()
	b := mustBucket(t, 4, 100*time.Millisecond, clock)

	if !b.AllowN(3) {
		t.Fatal("AllowN(3) with 4 tokens = false")
	}
	if b.AllowN(2) {
		t.Fatal("AllowN(2) with 1 token = true")
	}
	if !b.AllowN(1) {
		t.Fatal("AllowN(2) failing must not have consumed the remaining token")
	}
	if !b.AllowN(0) {
		t.Fatal("AllowN(0) = false, want true (nothing requested)")
	}
	if !b.AllowN(-1) {
		t.Fatal("AllowN(-1) = false, want true (nothing requested)")
	}
	clock.Advance(time.Hour)
	if b.AllowN(5) {
		t.Fatal("AllowN(n > capacity) = true; it can never be satisfied and must be false")
	}
	if !b.AllowN(4) {
		t.Fatal("the impossible AllowN(5) must not have consumed tokens")
	}
}

func TestWaitReturnsImmediatelyWhenTokenAvailable(t *testing.T) {
	clock := newFakeClock()
	b := mustBucket(t, 1, 100*time.Millisecond, clock)

	if err := b.Wait(context.Background()); err != nil {
		t.Fatalf("Wait with a token available: %v", err)
	}
	if got := clock.afterCalls(); got != 0 {
		t.Fatalf("Wait slept (%d After calls) even though a token was available", got)
	}
	if b.Allow() {
		t.Fatal("Wait returned nil but did not consume the token")
	}
}

func TestWaitSleepsExactlyUntilNextToken(t *testing.T) {
	clock := newFakeClock()
	b := mustBucket(t, 1, 100*time.Millisecond, clock)
	drain(t, b, 1)
	clock.Advance(30 * time.Millisecond) // 70ms of accrual still needed

	done := make(chan error, 1)
	go func() { done <- b.Wait(context.Background()) }()

	d := <-clock.created
	if d != 70*time.Millisecond {
		t.Fatalf("Wait slept for %v, want exactly the 70ms remaining until the next token", d)
	}
	clock.Advance(d)
	if err := <-done; err != nil {
		t.Fatalf("Wait after the sleep elapsed: %v", err)
	}
	if b.Allow() {
		t.Fatal("the token that accrued during Wait must have been consumed by Wait")
	}
}

func TestWaitHonorsContextCancel(t *testing.T) {
	clock := newFakeClock()
	b := mustBucket(t, 1, 100*time.Millisecond, clock)
	drain(t, b, 1)

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- b.Wait(ctx) }()

	<-clock.created // the waiter is parked on its timer
	cancel()
	if err := <-done; !errors.Is(err, context.Canceled) {
		t.Fatalf("Wait after cancel = %v, want context.Canceled", err)
	}

	// The canceled Wait must not have consumed the token that accrues later.
	clock.Advance(100 * time.Millisecond)
	if !b.Allow() {
		t.Fatal("canceled Wait consumed a token it never delivered")
	}
}

func TestConcurrentAllowGrantsExactlyCapacity(t *testing.T) {
	clock := newFakeClock()
	b := mustBucket(t, 100, time.Hour, clock)

	var granted int32
	var wg sync.WaitGroup
	for i := 0; i < 250; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if b.Allow() {
				atomic.AddInt32(&granted, 1)
			}
		}()
	}
	wg.Wait()
	if granted != 100 {
		t.Fatalf("concurrent Allow granted %d tokens, want exactly capacity (100)", granted)
	}
}
