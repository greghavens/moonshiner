package retry

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"
)

// sleepRecorder is an injectable Sleep that never touches wall-clock time.
type sleepRecorder struct {
	mu     sync.Mutex
	delays []time.Duration
}

func (s *sleepRecorder) sleep(ctx context.Context, d time.Duration) error {
	s.mu.Lock()
	s.delays = append(s.delays, d)
	s.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return err
	}
	return nil
}

func (s *sleepRecorder) recorded() []time.Duration {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]time.Duration(nil), s.delays...)
}

func failN(n int, then error, calls *int) func(context.Context) error {
	return func(context.Context) error {
		*calls++
		if *calls <= n {
			return fmt.Errorf("transient failure %d", *calls)
		}
		return then
	}
}

func TestFirstAttemptSuccessSleepsNever(t *testing.T) {
	rec := &sleepRecorder{}
	calls := 0
	err := Do(context.Background(), Options{
		MaxAttempts: 5,
		Backoff:     Constant(time.Second),
		Sleep:       rec.sleep,
	}, failN(0, nil, &calls))
	if err != nil {
		t.Fatalf("Do: %v", err)
	}
	if calls != 1 {
		t.Fatalf("fn called %d times, want 1", calls)
	}
	if len(rec.recorded()) != 0 {
		t.Fatalf("slept %v before/after a first-try success", rec.recorded())
	}
}

func TestRetriesUntilSuccessWithBackoffPerAttempt(t *testing.T) {
	rec := &sleepRecorder{}
	calls := 0
	err := Do(context.Background(), Options{
		MaxAttempts: 5,
		Backoff:     Constant(50 * time.Millisecond),
		Sleep:       rec.sleep,
	}, failN(2, nil, &calls))
	if err != nil {
		t.Fatalf("Do: %v", err)
	}
	if calls != 3 {
		t.Fatalf("fn called %d times, want 3", calls)
	}
	got := rec.recorded()
	want := []time.Duration{50 * time.Millisecond, 50 * time.Millisecond}
	if len(got) != len(want) {
		t.Fatalf("slept %d times (%v), want %d (one between each pair of attempts)", len(got), got, len(want))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("sleep %d = %v, want %v", i, got[i], want[i])
		}
	}
}

func TestExhaustionWrapsLastErrorAndReportsAttempts(t *testing.T) {
	rec := &sleepRecorder{}
	last := errors.New("disk quota exceeded")
	calls := 0
	fn := func(context.Context) error {
		calls++
		if calls < 3 {
			return fmt.Errorf("earlier failure %d", calls)
		}
		return last
	}
	err := Do(context.Background(), Options{
		MaxAttempts: 3,
		Backoff:     Constant(time.Millisecond),
		Sleep:       rec.sleep,
	}, fn)
	if err == nil {
		t.Fatal("Do succeeded after all attempts failed")
	}
	if !errors.Is(err, last) {
		t.Fatalf("exhaustion error must wrap the LAST attempt's error (errors.Is), got %v", err)
	}
	if !strings.Contains(err.Error(), "3 attempts") {
		t.Fatalf("exhaustion error should say \"3 attempts\", got %q", err)
	}
	if calls != 3 {
		t.Fatalf("fn called %d times, want exactly MaxAttempts (3)", calls)
	}
	if n := len(rec.recorded()); n != 2 {
		t.Fatalf("slept %d times, want 2 — never sleep after the final attempt", n)
	}
}

func TestNonRetryableErrorStopsImmediately(t *testing.T) {
	rec := &sleepRecorder{}
	fatal := errors.New("invalid credentials")
	calls := 0
	fn := func(context.Context) error {
		calls++
		if calls == 1 {
			return errors.New("connection reset")
		}
		return fatal
	}
	err := Do(context.Background(), Options{
		MaxAttempts: 10,
		Backoff:     Constant(time.Millisecond),
		Retryable:   func(err error) bool { return !errors.Is(err, fatal) },
		Sleep:       rec.sleep,
	}, fn)
	if !errors.Is(err, fatal) {
		t.Fatalf("want the non-retryable error back (errors.Is), got %v", err)
	}
	if calls != 2 {
		t.Fatalf("fn called %d times, want 2 (stop as soon as Retryable says no)", calls)
	}
	if n := len(rec.recorded()); n != 1 {
		t.Fatalf("slept %d times, want 1 — no backoff after a non-retryable error", n)
	}
}

func TestNilRetryableRetriesEverything(t *testing.T) {
	rec := &sleepRecorder{}
	calls := 0
	err := Do(context.Background(), Options{
		MaxAttempts: 4,
		Backoff:     Constant(time.Millisecond),
		Sleep:       rec.sleep,
	}, failN(99, nil, &calls))
	if err == nil {
		t.Fatal("expected exhaustion error")
	}
	if calls != 4 {
		t.Fatalf("nil Retryable should treat every error as retryable; fn called %d times, want 4", calls)
	}
}

func TestNilBackoffMeansNoSleeping(t *testing.T) {
	rec := &sleepRecorder{}
	calls := 0
	err := Do(context.Background(), Options{
		MaxAttempts: 3,
		Sleep:       rec.sleep,
	}, failN(99, nil, &calls))
	if err == nil {
		t.Fatal("expected exhaustion error")
	}
	if calls != 3 {
		t.Fatalf("fn called %d times, want 3", calls)
	}
	if n := len(rec.recorded()); n != 0 {
		t.Fatalf("nil Backoff must not sleep at all, slept %d times", n)
	}
}

func TestContextAlreadyCanceledRunsNothing(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	calls := 0
	err := Do(ctx, Options{MaxAttempts: 3, Sleep: (&sleepRecorder{}).sleep},
		failN(99, nil, &calls))
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("want context.Canceled, got %v", err)
	}
	if calls != 0 {
		t.Fatalf("fn must not run when ctx is already canceled; ran %d times", calls)
	}
}

func TestCancelDuringBackoffStopsFurtherAttempts(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	sleeping := make(chan struct{})
	blockingSleep := func(ctx context.Context, d time.Duration) error {
		close(sleeping)
		<-ctx.Done()
		return ctx.Err()
	}
	calls := 0
	done := make(chan error, 1)
	go func() {
		done <- Do(ctx, Options{
			MaxAttempts: 5,
			Backoff:     Constant(time.Hour),
			Sleep:       blockingSleep,
		}, failN(99, nil, &calls))
	}()
	<-sleeping
	cancel()
	err := <-done
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("want context.Canceled after cancel during backoff, got %v", err)
	}
	if calls != 1 {
		t.Fatalf("fn ran %d times, want 1 — cancel during backoff must prevent the next attempt", calls)
	}
}

func TestConstantPolicy(t *testing.T) {
	b := Constant(250 * time.Millisecond)
	for _, attempt := range []int{1, 2, 7} {
		if d := b(attempt); d != 250*time.Millisecond {
			t.Fatalf("Constant backoff attempt %d = %v, want 250ms", attempt, d)
		}
	}
}

func TestExponentialPolicyDoublesAndCaps(t *testing.T) {
	b := Exponential(100*time.Millisecond, 250*time.Millisecond)
	want := []time.Duration{
		100 * time.Millisecond, // attempt 1
		200 * time.Millisecond, // attempt 2
		250 * time.Millisecond, // attempt 3: 400ms capped
		250 * time.Millisecond, // attempt 4: stays at the cap
		250 * time.Millisecond,
	}
	for i, w := range want {
		if d := b(i + 1); d != w {
			t.Fatalf("Exponential attempt %d = %v, want %v", i+1, d, w)
		}
	}
}

func TestOptionValidation(t *testing.T) {
	calls := 0
	if err := Do(context.Background(), Options{MaxAttempts: 0}, failN(0, nil, &calls)); err == nil {
		t.Fatal("MaxAttempts 0 accepted")
	}
	if err := Do(context.Background(), Options{MaxAttempts: -2}, failN(0, nil, &calls)); err == nil {
		t.Fatal("negative MaxAttempts accepted")
	}
	if calls != 0 {
		t.Fatalf("fn ran %d times despite invalid options", calls)
	}
	if err := Do(context.Background(), Options{MaxAttempts: 1}, nil); err == nil {
		t.Fatal("nil fn accepted")
	}
}

func TestDefaultSleepIsWiredUp(t *testing.T) {
	// No injected Sleep: the package's real sleep must be used. Keep the
	// delay tiny; we only assert completion, not timing.
	calls := 0
	err := Do(context.Background(), Options{
		MaxAttempts: 2,
		Backoff:     Constant(time.Millisecond),
	}, failN(1, nil, &calls))
	if err != nil {
		t.Fatalf("Do with default sleep: %v", err)
	}
	if calls != 2 {
		t.Fatalf("fn called %d times, want 2", calls)
	}
}
