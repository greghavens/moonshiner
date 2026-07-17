package breaker

import (
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type fakeClock struct {
	mu  sync.Mutex
	now time.Time
}

func newFakeClock() *fakeClock {
	return &fakeClock{now: time.Date(2026, 5, 4, 12, 0, 0, 0, time.UTC)}
}

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *fakeClock) Advance(d time.Duration) {
	c.mu.Lock()
	c.now = c.now.Add(d)
	c.mu.Unlock()
}

var errUpstream = errors.New("upstream 503")

func failing() error { return errUpstream }
func succeeding() error { return nil }

func mustBreaker(t *testing.T, threshold int, cooldown time.Duration, clock Clock) *Breaker {
	t.Helper()
	b, err := New(threshold, cooldown, clock)
	if err != nil {
		t.Fatalf("New(%d, %v): %v", threshold, cooldown, err)
	}
	return b
}

// trip drives a fresh-enough breaker from closed to open.
func trip(t *testing.T, b *Breaker, threshold int) {
	t.Helper()
	for i := 0; i < threshold; i++ {
		if err := b.Do(failing); !errors.Is(err, errUpstream) {
			t.Fatalf("trip call %d: got %v, want the upstream error", i+1, err)
		}
	}
	if b.State() != StateOpen {
		t.Fatalf("state after %d consecutive failures = %v, want %v", threshold, b.State(), StateOpen)
	}
}

func TestNewValidation(t *testing.T) {
	clock := newFakeClock()
	if _, err := New(0, time.Second, clock); err == nil {
		t.Fatal("threshold 0 accepted")
	}
	if _, err := New(3, 0, clock); err == nil {
		t.Fatal("zero cooldown accepted")
	}
	if _, err := New(3, -time.Second, clock); err == nil {
		t.Fatal("negative cooldown accepted")
	}
	if _, err := New(3, time.Second, nil); err == nil {
		t.Fatal("nil clock accepted")
	}
}

func TestStartsClosedAndPassesResultsThrough(t *testing.T) {
	b := mustBreaker(t, 3, time.Second, newFakeClock())
	if b.State() != StateClosed {
		t.Fatalf("initial state = %v, want %v", b.State(), StateClosed)
	}
	if err := b.Do(succeeding); err != nil {
		t.Fatalf("Do(success) = %v, want nil", err)
	}
	if err := b.Do(failing); !errors.Is(err, errUpstream) {
		t.Fatalf("Do(failure) = %v, want the fn's own error back", err)
	}
	if b.State() != StateClosed {
		t.Fatalf("one failure below threshold flipped state to %v", b.State())
	}
}

func TestOpensAfterConsecutiveFailuresAndShortCircuits(t *testing.T) {
	b := mustBreaker(t, 3, time.Second, newFakeClock())
	trip(t, b, 3)

	var invoked int32
	err := b.Do(func() error { atomic.AddInt32(&invoked, 1); return nil })
	if !errors.Is(err, ErrOpen) {
		t.Fatalf("Do while open = %v, want ErrOpen", err)
	}
	if atomic.LoadInt32(&invoked) != 0 {
		t.Fatal("open breaker must not invoke the wrapped fn")
	}
}

func TestSuccessResetsConsecutiveFailureCount(t *testing.T) {
	b := mustBreaker(t, 3, time.Second, newFakeClock())
	b.Do(failing)
	b.Do(failing)
	b.Do(succeeding) // interrupts the streak
	b.Do(failing)
	b.Do(failing)
	if b.State() != StateClosed {
		t.Fatal("failures interrupted by a success are not consecutive; breaker must still be closed")
	}
	b.Do(failing)
	if b.State() != StateOpen {
		t.Fatal("third consecutive failure should open the breaker")
	}
}

func TestCooldownGatesTheTrialCall(t *testing.T) {
	clock := newFakeClock()
	b := mustBreaker(t, 2, 500*time.Millisecond, clock)
	trip(t, b, 2)

	clock.Advance(499 * time.Millisecond)
	if err := b.Do(succeeding); !errors.Is(err, ErrOpen) {
		t.Fatalf("1ms before cooldown expiry: got %v, want ErrOpen", err)
	}

	clock.Advance(1 * time.Millisecond) // exactly the cooldown boundary
	if b.State() != StateHalfOpen {
		t.Fatalf("state once cooldown elapsed = %v, want %v", b.State(), StateHalfOpen)
	}
	var invoked int32
	if err := b.Do(func() error { atomic.AddInt32(&invoked, 1); return nil }); err != nil {
		t.Fatalf("trial call: %v", err)
	}
	if atomic.LoadInt32(&invoked) != 1 {
		t.Fatal("trial call must actually invoke the wrapped fn")
	}
	if b.State() != StateClosed {
		t.Fatalf("state after successful trial = %v, want %v", b.State(), StateClosed)
	}
}

func TestTrialSuccessFullyResetsFailureBudget(t *testing.T) {
	clock := newFakeClock()
	b := mustBreaker(t, 2, time.Second, clock)
	trip(t, b, 2)
	clock.Advance(time.Second)
	if err := b.Do(succeeding); err != nil {
		t.Fatalf("trial: %v", err)
	}
	// A fully reset breaker needs a fresh streak of `threshold` failures.
	if err := b.Do(failing); !errors.Is(err, errUpstream) {
		t.Fatalf("first failure after recovery = %v", err)
	}
	if b.State() != StateClosed {
		t.Fatal("breaker reopened after a single failure; the trial success must reset the count")
	}
	b.Do(failing)
	if b.State() != StateOpen {
		t.Fatal("threshold consecutive failures after recovery should open again")
	}
}

func TestTrialFailureReopensWithFreshCooldown(t *testing.T) {
	clock := newFakeClock()
	b := mustBreaker(t, 2, time.Second, clock)
	trip(t, b, 2)

	clock.Advance(time.Second)
	if err := b.Do(failing); !errors.Is(err, errUpstream) {
		t.Fatalf("failing trial should return the fn's error, got %v", err)
	}
	if b.State() != StateOpen {
		t.Fatalf("state after failed trial = %v, want %v", b.State(), StateOpen)
	}
	// The cooldown restarts at the trial failure, not the original trip.
	clock.Advance(999 * time.Millisecond)
	if err := b.Do(succeeding); !errors.Is(err, ErrOpen) {
		t.Fatalf("cooldown after failed trial not restarted: got %v, want ErrOpen", err)
	}
	clock.Advance(1 * time.Millisecond)
	if err := b.Do(succeeding); err != nil {
		t.Fatalf("second trial after fresh cooldown: %v", err)
	}
	if b.State() != StateClosed {
		t.Fatalf("state = %v, want %v", b.State(), StateClosed)
	}
}

func TestHalfOpenAdmitsExactlyOneTrial(t *testing.T) {
	clock := newFakeClock()
	b := mustBreaker(t, 1, time.Second, clock)
	trip(t, b, 1)
	clock.Advance(time.Second)

	entered := make(chan struct{})
	release := make(chan struct{})
	trialErr := make(chan error, 1)
	var invocations int32
	go func() {
		trialErr <- b.Do(func() error {
			atomic.AddInt32(&invocations, 1)
			close(entered)
			<-release
			return nil
		})
	}()
	<-entered

	// While the trial is in flight, every other call is rejected without
	// touching the wrapped fn.
	for i := 0; i < 5; i++ {
		err := b.Do(func() error { atomic.AddInt32(&invocations, 1); return nil })
		if !errors.Is(err, ErrOpen) {
			t.Fatalf("call %d during in-flight trial = %v, want ErrOpen", i, err)
		}
	}
	if got := atomic.LoadInt32(&invocations); got != 1 {
		t.Fatalf("wrapped fn invoked %d times during half-open, want exactly the 1 trial", got)
	}

	close(release)
	if err := <-trialErr; err != nil {
		t.Fatalf("trial call: %v", err)
	}
	if b.State() != StateClosed {
		t.Fatalf("state after successful trial = %v, want %v", b.State(), StateClosed)
	}
}

func TestConcurrentClosedTrafficIsRaceFree(t *testing.T) {
	b := mustBreaker(t, 1000000, time.Second, newFakeClock())
	var wg sync.WaitGroup
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func(g int) {
			defer wg.Done()
			for i := 0; i < 300; i++ {
				if g%2 == 0 {
					b.Do(succeeding)
				} else {
					b.Do(failing)
				}
				_ = b.State()
			}
		}(g)
	}
	wg.Wait()
	if b.State() != StateClosed {
		t.Fatalf("state = %v, want closed (threshold was never reached: every other goroutine succeeds)", b.State())
	}
}
