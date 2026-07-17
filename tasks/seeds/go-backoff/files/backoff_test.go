package backoff

import (
	"testing"
	"time"
)

func TestDelaysGrowUntilCap(t *testing.T) {
	p := New(100*time.Millisecond, 2*time.Second, 0)
	want := []time.Duration{
		100 * time.Millisecond,
		200 * time.Millisecond,
		400 * time.Millisecond,
		800 * time.Millisecond,
		1600 * time.Millisecond,
		2 * time.Second,
		2 * time.Second,
	}
	for i, w := range want {
		if got := p.Next(); got != w {
			t.Fatalf("delay #%d = %v, want %v", i+1, got, w)
		}
	}
}

func TestAttemptTracksHandedOutDelays(t *testing.T) {
	p := New(50*time.Millisecond, time.Second, 0)
	for i := 0; i < 4; i++ {
		p.Next()
	}
	if got := p.Attempt(); got != 4 {
		t.Fatalf("Attempt() = %d after 4 delays, want 4", got)
	}
}

func TestResetStartsOver(t *testing.T) {
	p := New(100*time.Millisecond, 2*time.Second, 0)
	p.Next()
	p.Next()
	p.Next()
	p.Reset()
	if got := p.Next(); got != 100*time.Millisecond {
		t.Fatalf("first delay after Reset = %v, want %v", got, 100*time.Millisecond)
	}
	if got := p.Attempt(); got != 1 {
		t.Fatalf("Attempt() after Reset+Next = %d, want 1", got)
	}
}

func TestRetryBudget(t *testing.T) {
	p := New(10*time.Millisecond, time.Second, 3)
	for i := 0; i < 3; i++ {
		if p.Exhausted() {
			t.Fatalf("Exhausted() = true after only %d delays, budget is 3", i)
		}
		p.Next()
	}
	if !p.Exhausted() {
		t.Fatal("Exhausted() = false after using the full budget of 3")
	}
}

func TestTinyBaseNeverOverflows(t *testing.T) {
	p := New(time.Millisecond, 10*time.Second, 0)
	var last time.Duration
	for i := 0; i < 80; i++ {
		d := p.Next()
		if d <= 0 || d > 10*time.Second {
			t.Fatalf("delay #%d = %v, out of range (0, 10s]", i+1, d)
		}
		if d < last {
			t.Fatalf("delay #%d = %v shrank from %v", i+1, d, last)
		}
		last = d
	}
}
