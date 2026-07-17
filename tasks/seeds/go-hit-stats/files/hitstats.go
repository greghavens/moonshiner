// Package hitstats tracks per-route request counts for the admin
// dashboard. Every request handler calls Record on the hot path, while
// the dashboard polls Snapshot once a second, so reads vastly outnumber
// structural changes and the counters live behind a read/write lock.
package hitstats

import "sync"

// Stats accumulates request counts per route. Safe for concurrent use.
type Stats struct {
	mu     sync.RWMutex
	counts map[string]int
}

// New returns an empty Stats.
func New() *Stats {
	return &Stats{counts: make(map[string]int)}
}

// Record notes one request for the given route. Called from every
// in-flight request handler, so it has to stay cheap.
func (s *Stats) Record(route string) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	s.counts[route]++
}

// Total returns the number of requests recorded for one route.
func (s *Stats) Total(route string) int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.counts[route]
}

// Snapshot copies the current counters for the dashboard to render.
func (s *Stats) Snapshot() map[string]int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make(map[string]int, len(s.counts))
	for route, n := range s.counts {
		out[route] = n
	}
	return out
}

// Reset clears all counters (used by the nightly rollover job).
func (s *Stats) Reset() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.counts = make(map[string]int)
}
