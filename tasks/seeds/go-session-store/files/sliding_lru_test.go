package session

import (
	"testing"
	"time"
)

// Acceptance tests for the sliding-expiration mode and the MaxSessions
// LRU cap. The API contract: New takes variadic options, with Sliding()
// and MaxSessions(n) provided by this package.

func TestSlidingGetRenewsExpiry(t *testing.T) {
	s := New(10*time.Minute, Sliding())
	c := newFakeClock()
	s.now = c.now

	s.Set("sid1", map[string]string{"user": "ana"})
	// Keep touching the session every 6 minutes; each Get must push the
	// deadline out another full TTL, so it stays alive far past 10m.
	for i := 0; i < 4; i++ {
		c.advance(6 * time.Minute)
		if _, ok := s.Get("sid1"); !ok {
			t.Fatalf("sliding session expired despite being accessed (iteration %d)", i)
		}
	}
	// Now go idle past the TTL: it must die.
	c.advance(10*time.Minute + time.Second)
	if _, ok := s.Get("sid1"); ok {
		t.Fatal("sliding session survived past TTL with no accesses")
	}
}

func TestSlidingExpiredGetDoesNotRevive(t *testing.T) {
	s := New(10*time.Minute, Sliding())
	c := newFakeClock()
	s.now = c.now

	s.Set("sid1", map[string]string{"user": "ana"})
	c.advance(10*time.Minute + time.Second)
	if _, ok := s.Get("sid1"); ok {
		t.Fatal("expired session returned by Get")
	}
	if _, ok := s.Get("sid1"); ok {
		t.Fatal("expired session revived by a failed Get")
	}
	if got := s.Len(); got != 0 {
		t.Fatalf("Len() = %d after expiry, want 0", got)
	}
}

func TestSlidingDoesNotLeakIntoDefaultStores(t *testing.T) {
	s := New(10 * time.Minute) // no options: absolute expiry
	c := newFakeClock()
	s.now = c.now

	s.Set("sid1", map[string]string{"user": "ana"})
	c.advance(6 * time.Minute)
	s.Get("sid1")
	c.advance(6 * time.Minute)
	if _, ok := s.Get("sid1"); ok {
		t.Fatal("plain New(ttl) store gained sliding behavior")
	}
}

func TestMaxSessionsEvictsLeastRecentlyUsed(t *testing.T) {
	s := New(time.Hour, MaxSessions(2))
	c := newFakeClock()
	s.now = c.now

	s.Set("a", map[string]string{"n": "1"})
	c.advance(time.Second)
	s.Set("b", map[string]string{"n": "2"})
	c.advance(time.Second)
	if _, ok := s.Get("a"); !ok { // touch a: b is now the LRU entry
		t.Fatal("setup: a missing")
	}
	c.advance(time.Second)
	s.Set("c", map[string]string{"n": "3"})

	if _, ok := s.Get("b"); ok {
		t.Fatal("b should have been evicted as least recently used")
	}
	if _, ok := s.Get("a"); !ok {
		t.Fatal("a was evicted despite being recently used")
	}
	if _, ok := s.Get("c"); !ok {
		t.Fatal("c should be present right after Set")
	}
	if got := s.Len(); got != 2 {
		t.Fatalf("Len() = %d, want 2 (cap)", got)
	}
}

func TestUpdatingExistingSessionDoesNotEvict(t *testing.T) {
	s := New(time.Hour, MaxSessions(2))
	c := newFakeClock()
	s.now = c.now

	s.Set("a", map[string]string{"n": "1"})
	c.advance(time.Second)
	s.Set("b", map[string]string{"n": "2"})
	c.advance(time.Second)
	s.Set("a", map[string]string{"n": "1b"}) // update, not insert

	if got := s.Len(); got != 2 {
		t.Fatalf("Len() = %d after update, want 2 (update must not evict)", got)
	}
	// The update refreshed a's recency, so inserting d evicts b.
	// (No Get probes before this point: a Get would itself touch recency.)
	c.advance(time.Second)
	s.Set("d", map[string]string{"n": "4"})
	if _, ok := s.Get("b"); ok {
		t.Fatal("b should be the LRU entry after a was rewritten")
	}
	got, ok := s.Get("a")
	if !ok {
		t.Fatal("a should survive: it was written most recently before d")
	}
	if got["n"] != "1b" {
		t.Fatalf(`a's value = %q, want "1b" (the updated value)`, got["n"])
	}
	if _, ok := s.Get("d"); !ok {
		t.Fatal("d should be present right after insert")
	}
}

func TestSlidingAndMaxSessionsCompose(t *testing.T) {
	s := New(10*time.Minute, Sliding(), MaxSessions(2))
	c := newFakeClock()
	s.now = c.now

	s.Set("a", map[string]string{"n": "1"}) // t=0, absolute deadline would be t=10m
	c.advance(time.Minute)
	s.Set("b", map[string]string{"n": "2"}) // t=1m
	c.advance(time.Minute)
	if _, ok := s.Get("a"); !ok { // t=2m: renews a until t=12m, touches recency
		t.Fatal("setup: a missing")
	}
	c.advance(time.Minute)
	s.Set("c", map[string]string{"n": "3"}) // t=3m
	if _, ok := s.Get("b"); ok {
		t.Fatal("b should have been evicted (a was touched more recently)")
	}
	// At t=11m, a is dead under absolute expiry but alive under sliding
	// (deadline t=12m thanks to the Get at t=2m).
	c.advance(8 * time.Minute)
	if _, ok := s.Get("a"); !ok {
		t.Fatal("a's sliding deadline was not renewed by Get")
	}
}
