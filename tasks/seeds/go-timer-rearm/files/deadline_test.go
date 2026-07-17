package deadline

import (
	"testing"
	"time"
)

// fakeTimer implements Timer with time.Timer's documented semantics:
// one buffered tick, Stop reports false once the timer has fired, and
// firing a stopped timer delivers nothing. Fire is the test's stand-in
// for the deadline elapsing. resetWithTickPending records a Reset call
// made while an undrained tick sat in the channel, which the time.Timer
// documentation forbids.
type fakeTimer struct {
	ch                   chan time.Time
	running              bool
	resetWithTickPending bool
}

func newFakeTimer() *fakeTimer {
	return &fakeTimer{ch: make(chan time.Time, 1)}
}

func (f *fakeTimer) C() <-chan time.Time { return f.ch }

func (f *fakeTimer) Stop() bool {
	was := f.running
	f.running = false
	return was
}

func (f *fakeTimer) Reset(d time.Duration) {
	if len(f.ch) > 0 {
		f.resetWithTickPending = true
	}
	f.running = true
}

func (f *fakeTimer) Fire() {
	if !f.running {
		return
	}
	f.running = false
	select {
	case f.ch <- time.Time{}:
	default:
	}
}

func mustNotExpire(t *testing.T, w *Watchdog, when string) {
	t.Helper()
	if label, ok := w.Expired(); ok {
		t.Fatalf("%s: watchdog reported %q as expired", when, label)
	}
}

func mustExpire(t *testing.T, w *Watchdog, want string) {
	t.Helper()
	label, ok := w.Expired()
	if !ok {
		t.Fatalf("watchdog did not report %q as expired", want)
	}
	if label != want {
		t.Fatalf("expired label = %q, want %q", label, want)
	}
}

func TestReportsExpiryExactlyOnce(t *testing.T) {
	f := newFakeTimer()
	w := New(f)
	w.Arm("job-1", time.Second)
	mustNotExpire(t, w, "before the deadline fired")
	f.Fire()
	mustExpire(t, w, "job-1")
	mustNotExpire(t, w, "after the expiry was already reported")
}

func TestCancelledJobStaysQuiet(t *testing.T) {
	f := newFakeTimer()
	w := New(f)
	w.Arm("job-1", time.Second)
	w.Cancel()
	f.Fire()
	mustNotExpire(t, w, "after cancel")
}

func TestRearmAfterLapsedCancelDoesNotReplay(t *testing.T) {
	f := newFakeTimer()
	w := New(f)
	w.Arm("job-old", time.Second)
	f.Fire() // the old job's deadline lapses before anyone polls
	w.Cancel()
	mustNotExpire(t, w, "after cancelling the lapsed job")
	w.Arm("job-new", time.Second)
	mustNotExpire(t, w, "immediately after re-arming for a fresh job")
	if f.resetWithTickPending {
		t.Fatal("Reset was called while a tick was still queued, violating the Timer contract")
	}
	f.Fire()
	mustExpire(t, w, "job-new")
	mustNotExpire(t, w, "after the fresh expiry was already reported")
}

func TestRearmAfterConsumedExpiry(t *testing.T) {
	f := newFakeTimer()
	w := New(f)
	w.Arm("job-a", time.Second)
	f.Fire()
	mustExpire(t, w, "job-a")
	w.Arm("job-b", time.Second)
	mustNotExpire(t, w, "immediately after re-arming past a consumed expiry")
	f.Fire()
	mustExpire(t, w, "job-b")
}

func TestRearmReplacesPendingDeadline(t *testing.T) {
	f := newFakeTimer()
	w := New(f)
	w.Arm("job-a", time.Second)
	w.Arm("job-b", time.Second)
	if f.resetWithTickPending {
		t.Fatal("Reset was called while a tick was still queued, violating the Timer contract")
	}
	f.Fire()
	mustExpire(t, w, "job-b")
	mustNotExpire(t, w, "after the replacing job expired")
}

func TestRearmOverAnUnpolledLapsedJob(t *testing.T) {
	f := newFakeTimer()
	w := New(f)
	w.Arm("job-a", time.Second)
	f.Fire() // job-a lapses but the loop never polls before the next dispatch
	w.Arm("job-b", time.Second)
	mustNotExpire(t, w, "immediately after re-arming over an unpolled lapsed job")
	if f.resetWithTickPending {
		t.Fatal("Reset was called while a tick was still queued, violating the Timer contract")
	}
	f.Fire()
	mustExpire(t, w, "job-b")
}

func TestCancelAfterExpiryConsumed(t *testing.T) {
	f := newFakeTimer()
	w := New(f)
	w.Arm("job-a", time.Second)
	f.Fire()
	mustExpire(t, w, "job-a")
	w.Cancel() // no-op: nothing armed anymore
	w.Arm("job-b", time.Second)
	mustNotExpire(t, w, "after cancel of an already-consumed expiry")
	f.Fire()
	mustExpire(t, w, "job-b")
}
