package dlpool

import "time"

// Options tunes a Manager.
//
//   - BatchSize: how many files download concurrently; the next batch
//     starts only after the previous one has fully drained.
//   - Attempts: total tries per file before it is reported failed.
//   - BaseDelay / MaxDelay: retry pacing. The pause before a file's first
//     retry is BaseDelay, and it doubles for each further retry of that
//     file, capped at MaxDelay.
//   - Sleep: how to pause between attempts; injectable for tests and
//     defaults to time.Sleep.
type Options struct {
	BatchSize int
	Attempts  int
	BaseDelay time.Duration
	MaxDelay  time.Duration
	Sleep     func(time.Duration)
}

func withDefaults(opts Options) Options {
	if opts.BatchSize <= 0 {
		opts.BatchSize = 4
	}
	if opts.Attempts <= 0 {
		opts.Attempts = 3
	}
	if opts.BaseDelay <= 0 {
		opts.BaseDelay = 100 * time.Millisecond
	}
	if opts.MaxDelay <= 0 {
		opts.MaxDelay = 2 * time.Second
	}
	if opts.Sleep == nil {
		opts.Sleep = time.Sleep
	}
	return opts
}

// delayState tracks where the exponential retry pacing currently stands.
type delayState struct {
	next time.Duration
}

// nextDelay hands out the pause to take before the upcoming retry and
// advances the pacing state.
func (m *Manager) nextDelay() time.Duration {
	m.mu.Lock()
	defer m.mu.Unlock()
	d := m.delay.next
	m.delay.next *= 2
	if m.delay.next > m.opts.MaxDelay {
		m.delay.next = m.opts.MaxDelay
	}
	return d
}
