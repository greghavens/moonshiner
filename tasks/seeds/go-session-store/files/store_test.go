package session

import (
	"testing"
	"time"
)

// fakeClock drives the store's notion of time deterministically.
type fakeClock struct {
	t time.Time
}

func (c *fakeClock) now() time.Time          { return c.t }
func (c *fakeClock) advance(d time.Duration) { c.t = c.t.Add(d) }

func newFakeClock() *fakeClock {
	return &fakeClock{t: time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)}
}

func TestSetGetRoundTrip(t *testing.T) {
	s := New(time.Hour)
	s.Set("sid1", map[string]string{"user": "ana", "role": "admin"})
	got, ok := s.Get("sid1")
	if !ok {
		t.Fatal("Get(sid1) = miss, want hit")
	}
	if got["user"] != "ana" || got["role"] != "admin" {
		t.Fatalf("Get(sid1) = %v, want user=ana role=admin", got)
	}
	if _, ok := s.Get("nope"); ok {
		t.Fatal("Get(nope) = hit, want miss")
	}
}

func TestStoreCopiesValues(t *testing.T) {
	s := New(time.Hour)
	in := map[string]string{"user": "ana"}
	s.Set("sid1", in)
	in["user"] = "mallory"

	got, _ := s.Get("sid1")
	if got["user"] != "ana" {
		t.Fatalf("caller mutation leaked into store: user = %q", got["user"])
	}
	got["user"] = "mallory"
	again, _ := s.Get("sid1")
	if again["user"] != "ana" {
		t.Fatalf("mutating returned map leaked into store: user = %q", again["user"])
	}
}

func TestEntriesExpireAfterTTL(t *testing.T) {
	s := New(10 * time.Minute)
	c := newFakeClock()
	s.now = c.now

	s.Set("sid1", map[string]string{"user": "ana"})
	c.advance(9*time.Minute + 59*time.Second)
	if _, ok := s.Get("sid1"); !ok {
		t.Fatal("session expired before its TTL")
	}
	c.advance(2 * time.Second)
	if _, ok := s.Get("sid1"); ok {
		t.Fatal("session survived past its TTL")
	}
}

func TestGetDoesNotExtendLifetime(t *testing.T) {
	s := New(10 * time.Minute)
	c := newFakeClock()
	s.now = c.now

	s.Set("sid1", map[string]string{"user": "ana"})
	c.advance(6 * time.Minute)
	if _, ok := s.Get("sid1"); !ok {
		t.Fatal("session should still be alive at 6m")
	}
	c.advance(6 * time.Minute)
	if _, ok := s.Get("sid1"); ok {
		t.Fatal("default store must use absolute expiry: reads must not extend a session")
	}
}

func TestSetResetsExpiry(t *testing.T) {
	s := New(10 * time.Minute)
	c := newFakeClock()
	s.now = c.now

	s.Set("sid1", map[string]string{"n": "1"})
	c.advance(8 * time.Minute)
	s.Set("sid1", map[string]string{"n": "2"})
	c.advance(8 * time.Minute)
	got, ok := s.Get("sid1")
	if !ok {
		t.Fatal("rewritten session expired too early")
	}
	if got["n"] != "2" {
		t.Fatalf(`got n=%q, want "2"`, got["n"])
	}
}

func TestDeleteAndLen(t *testing.T) {
	s := New(10 * time.Minute)
	c := newFakeClock()
	s.now = c.now

	s.Set("a", map[string]string{"x": "1"})
	s.Set("b", map[string]string{"x": "2"})
	if got := s.Len(); got != 2 {
		t.Fatalf("Len() = %d, want 2", got)
	}
	s.Delete("a")
	if got := s.Len(); got != 1 {
		t.Fatalf("Len() after Delete = %d, want 1", got)
	}
	c.advance(11 * time.Minute)
	if got := s.Len(); got != 0 {
		t.Fatalf("Len() after expiry = %d, want 0", got)
	}
}
