// Package backoff paces retries for the outbound webhook dispatcher.
// Each failing endpoint gets its own Policy; the dispatcher asks it how
// long to sleep before the next attempt and resets it after a delivery
// finally succeeds.
package backoff

import "time"

// Policy computes exponential retry delays: base, 2*base, 4*base, ...
// capped at Max. After Limit attempts Exhausted reports true and the
// dispatcher parks the endpoint for manual review.
type Policy struct {
	Base    time.Duration
	Max     time.Duration
	Limit   int
	attempt int
}

// New returns a policy starting at base and never sleeping longer than max.
func New(base, max time.Duration, limit int) *Policy {
	return &Policy{Base: base, Max: max, Limit: limit}
}

// Next returns the delay to sleep before the upcoming attempt and advances
// the policy to the next stage.
func (p Policy) Next() time.Duration {
	d := p.Base << p.attempt
	if d <= 0 || d > p.Max {
		d = p.Max
	}
	p.attempt++
	return d
}

// Reset rewinds the policy after a successful delivery so the endpoint
// starts fresh at the base delay.
func (p Policy) Reset() {
	p.attempt = 0
}

// Attempt reports how many delays have been handed out since the last reset.
func (p *Policy) Attempt() int {
	return p.attempt
}

// Exhausted reports whether the endpoint has used up its retry budget.
func (p *Policy) Exhausted() bool {
	return p.Limit > 0 && p.attempt >= p.Limit
}
