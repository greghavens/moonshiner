package ttlmap

import (
	"fmt"
	"sync"
	"testing"
	"time"
)

type fakeClock struct {
	mu  sync.Mutex
	now time.Time
}

func newFakeClock() *fakeClock {
	return &fakeClock{now: time.Date(2026, 6, 15, 8, 0, 0, 0, time.UTC)}
}

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

func (c *fakeClock) Advance(d time.Duration) {
	c.mu.Lock()
	c.now = c.now.Add(d)
	c.mu.Unlock()
}

func mustMap(t *testing.T, clock Clock) *Map {
	t.Helper()
	m, err := New(clock)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return m
}

func TestNewRequiresClock(t *testing.T) {
	if _, err := New(nil); err == nil {
		t.Fatal("nil clock accepted")
	}
}

func TestSetGetRoundTrip(t *testing.T) {
	m := mustMap(t, newFakeClock())
	m.Set("greeting", "hello", time.Minute)
	v, ok := m.Get("greeting")
	if !ok || v != "hello" {
		t.Fatalf("Get = (%v, %v), want (hello, true)", v, ok)
	}
	if v, ok := m.Get("absent"); ok || v != nil {
		t.Fatalf("Get(absent) = (%v, %v), want (nil, false)", v, ok)
	}
}

func TestEntryExpiresExactlyAtDeadline(t *testing.T) {
	clock := newFakeClock()
	m := mustMap(t, clock)
	m.Set("token", "abc123", 100*time.Millisecond)

	clock.Advance(99 * time.Millisecond)
	if _, ok := m.Get("token"); !ok {
		t.Fatal("entry vanished 1ms before its deadline")
	}
	clock.Advance(1 * time.Millisecond)
	if v, ok := m.Get("token"); ok {
		t.Fatalf("entry still readable at its exact deadline: %v", v)
	}
}

func TestNonPositiveTTLNeverExpires(t *testing.T) {
	clock := newFakeClock()
	m := mustMap(t, clock)
	m.Set("pinned", 42, 0)
	m.Set("also-pinned", 43, -time.Hour)
	clock.Advance(1000 * time.Hour)
	if v, ok := m.Get("pinned"); !ok || v != 42 {
		t.Fatalf("ttl 0 entry expired: (%v, %v)", v, ok)
	}
	if v, ok := m.Get("also-pinned"); !ok || v != 43 {
		t.Fatalf("negative ttl entry expired: (%v, %v)", v, ok)
	}
}

func TestOverwriteReplacesValueAndRestartsTTL(t *testing.T) {
	clock := newFakeClock()
	m := mustMap(t, clock)
	m.Set("session", "v1", 100*time.Millisecond)
	clock.Advance(80 * time.Millisecond)
	m.Set("session", "v2", 100*time.Millisecond) // fresh deadline: now+100ms

	clock.Advance(80 * time.Millisecond) // 160ms after the first Set
	v, ok := m.Get("session")
	if !ok {
		t.Fatal("overwrite must restart the TTL from the moment of the overwrite")
	}
	if v != "v2" {
		t.Fatalf("Get = %v, want the overwritten value v2", v)
	}
	clock.Advance(20 * time.Millisecond) // 100ms after the overwrite
	if _, ok := m.Get("session"); ok {
		t.Fatal("entry outlived the overwrite's TTL")
	}
}

func TestGetOnExpiredEntryDeletesIt(t *testing.T) {
	clock := newFakeClock()
	m := mustMap(t, clock)
	m.Set("a", 1, 50*time.Millisecond)
	m.Set("b", 2, 50*time.Millisecond)
	m.Set("c", 3, 0)

	clock.Advance(50 * time.Millisecond)
	if _, ok := m.Get("a"); ok {
		t.Fatal("a should be expired")
	}
	// The expired read above must have removed "a" for real, so the sweep
	// only has "b" left to collect.
	if n := m.Sweep(); n != 1 {
		t.Fatalf("Sweep = %d, want 1 — Get on an expired key must delete it, not just hide it", n)
	}
}

func TestSweepRemovesAllExpiredAndReportsCount(t *testing.T) {
	clock := newFakeClock()
	m := mustMap(t, clock)
	m.Set("short-1", 1, 10*time.Millisecond)
	m.Set("short-2", 2, 20*time.Millisecond)
	m.Set("long", 3, time.Hour)
	m.Set("forever", 4, 0)

	if n := m.Sweep(); n != 0 {
		t.Fatalf("Sweep with nothing expired = %d, want 0", n)
	}
	clock.Advance(30 * time.Millisecond)
	if n := m.Sweep(); n != 2 {
		t.Fatalf("Sweep = %d, want 2", n)
	}
	if n := m.Sweep(); n != 0 {
		t.Fatalf("second Sweep = %d, want 0 (already collected)", n)
	}
	if _, ok := m.Get("long"); !ok {
		t.Fatal("Sweep removed a live entry")
	}
	if _, ok := m.Get("forever"); !ok {
		t.Fatal("Sweep removed a never-expiring entry")
	}
}

func TestLenCountsOnlyLiveEntries(t *testing.T) {
	clock := newFakeClock()
	m := mustMap(t, clock)
	m.Set("a", 1, 10*time.Millisecond)
	m.Set("b", 2, 10*time.Millisecond)
	m.Set("c", 3, time.Hour)
	if got := m.Len(); got != 3 {
		t.Fatalf("Len = %d, want 3", got)
	}
	clock.Advance(10 * time.Millisecond)
	// No Get, no Sweep — Len itself must not count corpses.
	if got := m.Len(); got != 1 {
		t.Fatalf("Len = %d, want 1 (expired-but-unswept entries must not count)", got)
	}
}

func TestKeysSortedAndLiveOnly(t *testing.T) {
	clock := newFakeClock()
	m := mustMap(t, clock)
	m.Set("zebra", 1, time.Hour)
	m.Set("apple", 2, 10*time.Millisecond)
	m.Set("mango", 3, 0)
	clock.Advance(20 * time.Millisecond)
	got := m.Keys()
	want := []string{"mango", "zebra"}
	if len(got) != len(want) {
		t.Fatalf("Keys = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("Keys = %v, want %v (sorted, live entries only)", got, want)
		}
	}
}

func TestDeleteReportsLivePresenceOnly(t *testing.T) {
	clock := newFakeClock()
	m := mustMap(t, clock)
	m.Set("live", 1, time.Hour)
	m.Set("dying", 2, 10*time.Millisecond)
	if !m.Delete("live") {
		t.Fatal("Delete(live) = false, want true")
	}
	if m.Delete("live") {
		t.Fatal("second Delete = true, want false")
	}
	clock.Advance(10 * time.Millisecond)
	if m.Delete("dying") {
		t.Fatal("Delete on an expired entry = true; expired entries are logically gone")
	}
	if m.Delete("never-was") {
		t.Fatal("Delete(missing) = true")
	}
}

func TestConcurrentReadersWritersAndSweepers(t *testing.T) {
	clock := newFakeClock()
	m := mustMap(t, clock)
	var wg sync.WaitGroup
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func(g int) {
			defer wg.Done()
			for i := 0; i < 300; i++ {
				key := fmt.Sprintf("g%d-k%d", g, i%20)
				switch i % 4 {
				case 0:
					m.Set(key, i, time.Duration(i%3)*time.Millisecond) // mix of expiring and pinned
				case 1:
					m.Get(key)
				case 2:
					m.Sweep()
				case 3:
					if i%20 == 3 {
						clock.Advance(time.Millisecond)
					}
					m.Len()
				}
			}
		}(g)
	}
	wg.Wait()

	// Deterministic tail: after the dust settles, pinned entries written
	// now must survive a big advance and a sweep.
	m.Set("anchor", "stays", 0)
	clock.Advance(time.Hour)
	m.Sweep()
	if v, ok := m.Get("anchor"); !ok || v != "stays" {
		t.Fatalf("anchor entry = (%v, %v), want (stays, true)", v, ok)
	}
}
