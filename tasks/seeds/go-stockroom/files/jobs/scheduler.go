// Package jobs is a small logical-time job scheduler. Nothing here
// spawns goroutines: the service's run loop (or a test) calls Tick and
// the scheduler runs whatever has come due on the injected clock.
package jobs

import (
	"errors"
	"sort"
	"time"
)

// Clock is injectable so jobs are testable at logical times.
type Clock interface {
	Now() time.Time
}

// ErrBusy is returned by a job that cannot run right now (for example a
// counting session is open); the scheduler retries it after RetryDelay.
var ErrBusy = errors.New("job busy, retry later")

// Job is a unit of scheduled work. Every > 0 makes it periodic.
type Job struct {
	Name  string
	Every time.Duration
	Run   func(now time.Time) error
}

type entry struct {
	job Job
	due time.Time
}

type Scheduler struct {
	clock      Clock
	retryDelay time.Duration
	queue      []entry
}

func NewScheduler(clock Clock, retryDelay time.Duration) *Scheduler {
	return &Scheduler{clock: clock, retryDelay: retryDelay}
}

// Add queues a job; it becomes due immediately (next Tick).
func (s *Scheduler) Add(job Job) {
	s.queue = append(s.queue, entry{job: job, due: s.clock.Now()})
}

// Pending lists queued jobs as "name@RFC3339", ordered by due time then
// name. Ops dumps this next to the ledger when a night looks off.
func (s *Scheduler) Pending() []string {
	sorted := make([]entry, len(s.queue))
	copy(sorted, s.queue)
	sortEntries(sorted)
	out := make([]string, len(sorted))
	for i, e := range sorted {
		out[i] = e.job.Name + "@" + e.due.UTC().Format(time.RFC3339)
	}
	return out
}

// Tick runs every job due at the injected clock's current time and
// returns their names in execution order. Periodic jobs are rescheduled
// one interval out; a job that returned ErrBusy is retried after
// RetryDelay.
func (s *Scheduler) Tick() []string {
	now := s.clock.Now()
	var due, rest []entry
	for _, e := range s.queue {
		if e.due.After(now) {
			rest = append(rest, e)
		} else {
			due = append(due, e)
		}
	}
	sortEntries(due)
	s.queue = rest

	ran := make([]string, 0, len(due))
	for _, e := range due {
		err := e.job.Run(now)
		if errors.Is(err, ErrBusy) {
			s.queue = append(s.queue, entry{job: e.job, due: now.Add(s.retryDelay)})
		}
		if e.job.Every > 0 {
			s.queue = append(s.queue, entry{job: e.job, due: now.Add(e.job.Every)})
		}
		ran = append(ran, e.job.Name)
	}
	return ran
}

func sortEntries(entries []entry) {
	sort.Slice(entries, func(i, j int) bool {
		if !entries[i].due.Equal(entries[j].due) {
			return entries[i].due.Before(entries[j].due)
		}
		return entries[i].job.Name < entries[j].job.Name
	})
}
