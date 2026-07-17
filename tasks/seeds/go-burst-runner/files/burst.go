// Package burst fires a batch of prepared requests at the same instant.
// The load-testing CLI queues one task per virtual user, then calls Fire
// to release them simultaneously and waits for the burst to finish so it
// can read the latency histogram afterwards.
package burst

import "sync"

// Runner holds a batch of queued tasks awaiting the starting gun.
type Runner struct {
	start  chan struct{}
	wg     sync.WaitGroup
	queued int
}

// New returns an empty Runner. A Runner is single-shot: queue tasks, Fire
// once, then build a fresh Runner for the next burst.
func New() *Runner {
	return &Runner{start: make(chan struct{})}
}

// Queue registers fn to run when Fire is called. Tasks must not start
// early — the whole point is that every virtual user hits the target at
// the same moment.
func (r *Runner) Queue(fn func()) {
	r.queued++
	go func() {
		<-r.start // hold until the whole batch is released
		r.wg.Add(1)
		defer r.wg.Done()
		fn()
	}()
}

// Queued reports how many tasks are waiting for the starting gun.
func (r *Runner) Queued() int {
	return r.queued
}

// Fire releases every queued task at once and blocks until they have all
// completed, so callers can safely read whatever the tasks recorded.
func (r *Runner) Fire() {
	close(r.start)
	r.wg.Wait()
}
