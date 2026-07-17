// Package registry tracks live service health for an internal status
// dashboard. Probe goroutines push updates while dashboard handlers
// pull point-in-time snapshots, so the registry must be safe for
// concurrent use.
package registry

import "sync"

// Status is one service's most recent health report.
type Status struct {
	State  string
	Detail string
}

// Watcher is notified after every recorded update. Watchers may call
// back into the registry (dashboards re-read state on change).
type Watcher func(name string, s Status)

// Registry holds the current status of every known service.
type Registry struct {
	mu       sync.Mutex
	services map[string]Status
	watchers []Watcher
}

// New returns an empty registry.
func New() *Registry {
	return &Registry{services: make(map[string]Status)}
}

// Update records a new status for name and notifies every watcher.
func (r *Registry) Update(name string, s Status) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.services[name] = s
	for _, w := range r.watchers {
		w(name, s)
	}
}

// Get returns the current status for one service.
func (r *Registry) Get(name string) (Status, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	s, ok := r.services[name]
	return s, ok
}

// Watch registers a watcher for all future updates.
func (r *Registry) Watch(w Watcher) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.watchers = append(r.watchers, w)
}

// Snapshot returns a point-in-time view of every service. The caller
// owns the returned map; later updates must not show through, and
// edits to it must not touch the registry.
func (r *Registry) Snapshot() map[string]Status {
	return r.services
}
