// Package session provides the in-memory session store backing the web
// console's login sessions. Entries carry a TTL and expire lazily: an
// expired session is dropped the next time it is touched.
package session

import (
	"sync"
	"time"
)

// Store holds session data keyed by session ID. It is safe for
// concurrent use.
type Store struct {
	mu   sync.Mutex
	ttl  time.Duration
	data map[string]*entry
	now  func() time.Time // swapped out in tests
}

type entry struct {
	values    map[string]string
	expiresAt time.Time
}

// New returns an empty store whose entries expire ttl after they were
// last written.
func New(ttl time.Duration) *Store {
	return &Store{
		ttl:  ttl,
		data: make(map[string]*entry),
		now:  time.Now,
	}
}

// Set stores values under the session id, replacing any previous entry
// and resetting its expiry. The map is copied; later changes by the
// caller do not leak into the store.
func (s *Store) Set(id string, values map[string]string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	copied := make(map[string]string, len(values))
	for k, v := range values {
		copied[k] = v
	}
	s.data[id] = &entry{values: copied, expiresAt: s.now().Add(s.ttl)}
}

// Get returns a copy of the values for id, or ok=false if the session
// does not exist or has expired.
func (s *Store) Get(id string) (map[string]string, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	e, ok := s.data[id]
	if !ok {
		return nil, false
	}
	if !s.now().Before(e.expiresAt) {
		delete(s.data, id)
		return nil, false
	}
	out := make(map[string]string, len(e.values))
	for k, v := range e.values {
		out[k] = v
	}
	return out, true
}

// Delete removes the session if present.
func (s *Store) Delete(id string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.data, id)
}

// Len reports the number of live (non-expired) sessions.
func (s *Store) Len() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	n := 0
	for _, e := range s.data {
		if s.now().Before(e.expiresAt) {
			n++
		}
	}
	return n
}
