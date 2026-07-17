package stockroom

import "time"

// Config tunes the service. Zero values fall back to defaults.
type Config struct {
	// RetryDelay is how long the scheduler waits before retrying a job
	// that reported busy (counting session open).
	RetryDelay time.Duration
	// ReconcileEvery is the reconciliation cadence per warehouse.
	ReconcileEvery time.Duration
}

func (c Config) withDefaults() Config {
	if c.RetryDelay <= 0 {
		c.RetryDelay = 15 * time.Minute
	}
	if c.ReconcileEvery <= 0 {
		c.ReconcileEvery = 24 * time.Hour
	}
	return c
}
