package quota

import (
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestMonthRolloverResetsCounters(t *testing.T) {
	clk := newFakeClock(on(2026, time.January, 15, 9))
	s := NewService(clk.Now)
	mustDefine(t, s, Plan{Name: "starter", Limits: map[string]int64{"api-calls": 10}})
	mustSetAccount(t, s, "acme", "starter")

	mustRecord(t, s, "acme", "api-calls", 9)
	clk.Set(on(2026, time.January, 31, 23))
	wantDecision(t, mustRecord(t, s, "acme", "api-calls", 1), true, false, 0)
	wantDecision(t, mustRecord(t, s, "acme", "api-calls", 1), false, true, 0)

	// New month, fresh counters — no explicit reset call anywhere.
	clk.Set(on(2026, time.February, 1, 0))
	u, err := s.Usage("acme")
	if err != nil {
		t.Fatalf("Usage: %v", err)
	}
	if len(u) != 0 {
		t.Fatalf("usage after month rollover = %v, want empty", u)
	}
	wantDecision(t, mustRecord(t, s, "acme", "api-calls", 4), true, false, 6)

	got, err := s.Report("acme")
	if err != nil {
		t.Fatalf("Report: %v", err)
	}
	if len(got) != 1 || got[0] != (MetricReport{Metric: "api-calls", Used: 4, Limit: 10, Remaining: 6, OverLimit: false}) {
		t.Fatalf("Report after rollover = %+v, want fresh february numbers", got)
	}
}

func TestSameMonthDoesNotReset(t *testing.T) {
	clk := newFakeClock(on(2026, time.March, 2, 8))
	s := NewService(clk.Now)
	mustDefine(t, s, Plan{Name: "starter", Limits: map[string]int64{"api-calls": 10}})
	mustSetAccount(t, s, "acme", "starter")

	mustRecord(t, s, "acme", "api-calls", 5)
	clk.Set(on(2026, time.March, 28, 22))
	if got := usageOf(t, s, "acme", "api-calls"); got != 5 {
		t.Fatalf("usage later in the same month = %d, want 5", got)
	}
	wantDecision(t, mustRecord(t, s, "acme", "api-calls", 5), true, false, 0)
}

func TestYearBoundaryRollover(t *testing.T) {
	clk := newFakeClock(on(2025, time.December, 20, 12))
	s := NewService(clk.Now)
	mustDefine(t, s, Plan{Name: "starter", Limits: map[string]int64{"api-calls": 10}})
	mustSetAccount(t, s, "acme", "starter")

	mustRecord(t, s, "acme", "api-calls", 10)
	clk.Set(on(2026, time.January, 3, 12))
	wantDecision(t, mustRecord(t, s, "acme", "api-calls", 10), true, false, 0)
	if got := usageOf(t, s, "acme", "api-calls"); got != 10 {
		t.Fatalf("january usage = %d, want 10 (december must not carry over)", got)
	}
}

func TestPlanChangeMidPeriodKeepsUsage(t *testing.T) {
	clk := newFakeClock(on(2026, time.January, 15, 9))
	s := NewService(clk.Now)
	mustDefine(t, s, Plan{Name: "starter", Limits: map[string]int64{"api-calls": 10}})
	mustDefine(t, s, Plan{Name: "pro", Limits: map[string]int64{"api-calls": 100}})
	mustSetAccount(t, s, "acme", "starter")

	mustRecord(t, s, "acme", "api-calls", 8)
	mustSetAccount(t, s, "acme", "pro") // upgrade mid-month

	if got := usageOf(t, s, "acme", "api-calls"); got != 8 {
		t.Fatalf("usage after plan change = %d, want 8 (upgrade must not wipe the meter)", got)
	}
	// The new, larger limit applies to the existing usage.
	wantDecision(t, mustRecord(t, s, "acme", "api-calls", 90), true, false, 2)
}

func TestConcurrentRecordsNeverOvershootABlockLimit(t *testing.T) {
	clk := newFakeClock(on(2026, time.January, 15, 9))
	s := NewService(clk.Now)
	mustDefine(t, s, Plan{
		Name:    "starter",
		Limits:  map[string]int64{"api-calls": 50},
		Overage: map[string]OveragePolicy{"api-calls": OverageBlock},
	})
	mustSetAccount(t, s, "acme", "starter")

	const attempts = 100
	var allowed, blocked atomic.Int64
	var wg sync.WaitGroup
	for i := 0; i < attempts; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			d, err := s.Record("acme", "api-calls", 1)
			if err != nil {
				t.Errorf("concurrent Record: %v", err)
				return
			}
			if d.Allowed {
				allowed.Add(1)
			} else {
				blocked.Add(1)
			}
		}()
	}
	wg.Wait()

	if got := allowed.Load(); got != 50 {
		t.Fatalf("%d requests allowed, want exactly 50 (check-and-increment must be atomic)", got)
	}
	if got := blocked.Load(); got != 50 {
		t.Fatalf("%d requests blocked, want exactly 50", got)
	}
	if got := usageOf(t, s, "acme", "api-calls"); got != 50 {
		t.Fatalf("usage = %d, want exactly the limit (50)", got)
	}
}
