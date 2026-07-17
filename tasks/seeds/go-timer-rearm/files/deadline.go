// Package deadline provides the per-job watchdog used by the task
// scheduler's dispatch loop. The loop polls Expired every tick and
// requeues any job whose deadline lapsed.
package deadline

import "time"

// Timer is the subset of time.Timer the watchdog needs; the scheduler
// injects a real timer in production and tests inject a deterministic
// one. Semantics mirror time.Timer exactly: the tick channel is
// buffered and holds at most one pending tick, Stop reports false when
// the timer already fired, and Reset re-arms the timer.
type Timer interface {
	C() <-chan time.Time
	Stop() bool
	Reset(d time.Duration)
}

// realTimer adapts time.Timer to the Timer interface.
type realTimer struct{ t *time.Timer }

// NewRealTimer returns a Timer backed by the runtime clock.
func NewRealTimer(d time.Duration) Timer {
	return &realTimer{t: time.NewTimer(d)}
}

func (r *realTimer) C() <-chan time.Time  { return r.t.C }
func (r *realTimer) Stop() bool           { return r.t.Stop() }
func (r *realTimer) Reset(d time.Duration) { r.t.Reset(d) }

// Watchdog guards one in-flight job at a time. It is rearmed for every
// job the dispatch loop hands out.
type Watchdog struct {
	timer Timer
	label string
	armed bool
}

// New returns a watchdog driven by t.
func New(t Timer) *Watchdog {
	return &Watchdog{timer: t}
}

// Arm starts (or restarts) the watchdog for the named job. Any
// previous deadline is discarded: only the new job may be reported.
func (w *Watchdog) Arm(label string, d time.Duration) {
	if w.armed {
		w.timer.Stop()
	}
	w.timer.Reset(d)
	w.label = label
	w.armed = true
}

// Cancel disarms the watchdog when its job finishes in time. A
// cancelled job's deadline must never surface, not even after a later
// re-arm.
func (w *Watchdog) Cancel() {
	if !w.armed {
		return
	}
	w.timer.Stop()
	w.armed = false
	w.label = ""
}

// Expired reports whether the armed deadline has fired. It returns the
// job label at most once per Arm and never blocks.
func (w *Watchdog) Expired() (string, bool) {
	if !w.armed {
		return "", false
	}
	select {
	case <-w.timer.C():
		w.armed = false
		return w.label, true
	default:
		return "", false
	}
}
