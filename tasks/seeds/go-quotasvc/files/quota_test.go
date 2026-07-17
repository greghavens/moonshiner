package quota

import (
	"sync"
	"testing"
	"time"
)

// fakeClock is hand-cranked: tests move it, nothing sleeps.
type fakeClock struct {
	mu sync.Mutex
	t  time.Time
}

func newFakeClock(start time.Time) *fakeClock { return &fakeClock{t: start} }

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *fakeClock) Set(t time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.t = t
}

func on(y int, m time.Month, d, hour int) time.Time {
	return time.Date(y, m, d, hour, 0, 0, 0, time.UTC)
}

func mustDefine(t *testing.T, s *Service, p Plan) {
	t.Helper()
	if err := s.DefinePlan(p); err != nil {
		t.Fatalf("DefinePlan(%q): %v", p.Name, err)
	}
}

func mustSetAccount(t *testing.T, s *Service, account, plan string) {
	t.Helper()
	if err := s.SetAccount(account, plan); err != nil {
		t.Fatalf("SetAccount(%q, %q): %v", account, plan, err)
	}
}

func mustRecord(t *testing.T, s *Service, account, metric string, n int64) Decision {
	t.Helper()
	d, err := s.Record(account, metric, n)
	if err != nil {
		t.Fatalf("Record(%q, %q, %d): %v", account, metric, n, err)
	}
	return d
}

func wantDecision(t *testing.T, got Decision, allowed, over bool, remaining int64) {
	t.Helper()
	want := Decision{Allowed: allowed, OverLimit: over, Remaining: remaining}
	if got != want {
		t.Fatalf("Decision = %+v, want %+v", got, want)
	}
}

func usageOf(t *testing.T, s *Service, account, metric string) int64 {
	t.Helper()
	u, err := s.Usage(account)
	if err != nil {
		t.Fatalf("Usage(%q): %v", account, err)
	}
	return u[metric]
}

func TestRecordWithinLimit(t *testing.T) {
	clk := newFakeClock(on(2026, time.January, 15, 9))
	s := NewService(clk.Now)
	mustDefine(t, s, Plan{Name: "starter", Limits: map[string]int64{"api-calls": 10}})
	mustSetAccount(t, s, "acme", "starter")

	wantDecision(t, mustRecord(t, s, "acme", "api-calls", 3), true, false, 7)
	wantDecision(t, mustRecord(t, s, "acme", "api-calls", 7), true, false, 0)
	if got := usageOf(t, s, "acme", "api-calls"); got != 10 {
		t.Fatalf("usage = %d, want 10", got)
	}
}

func TestBlockPolicyIsAllOrNothing(t *testing.T) {
	clk := newFakeClock(on(2026, time.January, 15, 9))
	s := NewService(clk.Now)
	mustDefine(t, s, Plan{
		Name:    "starter",
		Limits:  map[string]int64{"api-calls": 10},
		Overage: map[string]OveragePolicy{"api-calls": OverageBlock},
	})
	mustSetAccount(t, s, "acme", "starter")

	mustRecord(t, s, "acme", "api-calls", 8)
	// 8 used, asking for 5: would land at 13 — the whole request is refused.
	wantDecision(t, mustRecord(t, s, "acme", "api-calls", 5), false, true, 2)
	if got := usageOf(t, s, "acme", "api-calls"); got != 8 {
		t.Fatalf("usage after blocked request = %d, want 8 (no partial consumption)", got)
	}
	// A request that fits still goes through afterwards.
	wantDecision(t, mustRecord(t, s, "acme", "api-calls", 2), true, false, 0)
}

func TestAllowFlaggedPolicyAccumulatesOverage(t *testing.T) {
	clk := newFakeClock(on(2026, time.January, 15, 9))
	s := NewService(clk.Now)
	mustDefine(t, s, Plan{
		Name:    "pro",
		Limits:  map[string]int64{"api-calls": 100},
		Overage: map[string]OveragePolicy{"api-calls": OverageAllowFlagged},
	})
	mustSetAccount(t, s, "acme", "pro")

	wantDecision(t, mustRecord(t, s, "acme", "api-calls", 90), true, false, 10)
	wantDecision(t, mustRecord(t, s, "acme", "api-calls", 20), true, true, 0)
	if got := usageOf(t, s, "acme", "api-calls"); got != 110 {
		t.Fatalf("usage = %d, want 110 (allow-with-flag keeps counting)", got)
	}
}

func TestOverageDefaultsToBlock(t *testing.T) {
	clk := newFakeClock(on(2026, time.January, 15, 9))
	s := NewService(clk.Now)
	mustDefine(t, s, Plan{Name: "starter", Limits: map[string]int64{"exports": 2}})
	mustSetAccount(t, s, "acme", "starter")

	mustRecord(t, s, "acme", "exports", 2)
	wantDecision(t, mustRecord(t, s, "acme", "exports", 1), false, true, 0)
	if got := usageOf(t, s, "acme", "exports"); got != 2 {
		t.Fatalf("usage = %d, want 2", got)
	}
}

func TestUnlimitedMetric(t *testing.T) {
	clk := newFakeClock(on(2026, time.January, 15, 9))
	s := NewService(clk.Now)
	mustDefine(t, s, Plan{Name: "starter", Limits: map[string]int64{"api-calls": 10}})
	mustSetAccount(t, s, "acme", "starter")

	// "webhooks" has no limit in the plan: always allowed, Remaining -1.
	wantDecision(t, mustRecord(t, s, "acme", "webhooks", 1000), true, false, -1)
	wantDecision(t, mustRecord(t, s, "acme", "webhooks", 1), true, false, -1)
	if got := usageOf(t, s, "acme", "webhooks"); got != 1001 {
		t.Fatalf("usage = %d, want 1001 (unlimited still counts usage)", got)
	}
}

func TestAccountsAreIsolated(t *testing.T) {
	clk := newFakeClock(on(2026, time.January, 15, 9))
	s := NewService(clk.Now)
	mustDefine(t, s, Plan{Name: "starter", Limits: map[string]int64{"api-calls": 10}})
	mustSetAccount(t, s, "acme", "starter")
	mustSetAccount(t, s, "globex", "starter")

	mustRecord(t, s, "acme", "api-calls", 9)
	wantDecision(t, mustRecord(t, s, "globex", "api-calls", 1), true, false, 9)
	if got := usageOf(t, s, "globex", "api-calls"); got != 1 {
		t.Fatalf("globex usage = %d, want 1 (acme's usage must not leak)", got)
	}
}

func TestReportSortedWithLimitsAndFlags(t *testing.T) {
	clk := newFakeClock(on(2026, time.January, 15, 9))
	s := NewService(clk.Now)
	mustDefine(t, s, Plan{
		Name:    "team",
		Limits:  map[string]int64{"exports": 5, "api-calls": 10, "seats": 3},
		Overage: map[string]OveragePolicy{"api-calls": OverageAllowFlagged},
	})
	mustSetAccount(t, s, "acme", "team")

	mustRecord(t, s, "acme", "api-calls", 12) // allow-flagged: lands at 12/10
	mustRecord(t, s, "acme", "exports", 2)
	mustRecord(t, s, "acme", "webhooks", 40) // unlimited metric with usage

	got, err := s.Report("acme")
	if err != nil {
		t.Fatalf("Report: %v", err)
	}
	want := []MetricReport{
		{Metric: "api-calls", Used: 12, Limit: 10, Remaining: 0, OverLimit: true},
		{Metric: "exports", Used: 2, Limit: 5, Remaining: 3, OverLimit: false},
		{Metric: "seats", Used: 0, Limit: 3, Remaining: 3, OverLimit: false},
		{Metric: "webhooks", Used: 40, Limit: -1, Remaining: -1, OverLimit: false},
	}
	if len(got) != len(want) {
		t.Fatalf("Report = %+v, want %+v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("Report[%d] = %+v, want %+v", i, got[i], want[i])
		}
	}
}

func TestUsageReturnsACopy(t *testing.T) {
	clk := newFakeClock(on(2026, time.January, 15, 9))
	s := NewService(clk.Now)
	mustDefine(t, s, Plan{Name: "starter", Limits: map[string]int64{"api-calls": 10}})
	mustSetAccount(t, s, "acme", "starter")
	mustRecord(t, s, "acme", "api-calls", 4)

	u, err := s.Usage("acme")
	if err != nil {
		t.Fatalf("Usage: %v", err)
	}
	u["api-calls"] = 999 // caller scribbles on the returned map
	if got := usageOf(t, s, "acme", "api-calls"); got != 4 {
		t.Fatalf("internal usage = %d after caller mutation, want 4", got)
	}
}

func TestValidationErrors(t *testing.T) {
	clk := newFakeClock(on(2026, time.January, 15, 9))
	s := NewService(clk.Now)
	mustDefine(t, s, Plan{Name: "starter", Limits: map[string]int64{"api-calls": 10}})

	if err := s.DefinePlan(Plan{Name: ""}); err == nil {
		t.Fatal("DefinePlan with empty name must return an error")
	}
	if err := s.DefinePlan(Plan{Name: "bad", Limits: map[string]int64{"x": -5}}); err == nil {
		t.Fatal("DefinePlan with a negative limit must return an error")
	}
	if err := s.DefinePlan(Plan{Name: "starter", Limits: map[string]int64{"api-calls": 99}}); err == nil {
		t.Fatal("redefining an existing plan must return an error")
	}
	if err := s.SetAccount("acme", "no-such-plan"); err == nil {
		t.Fatal("SetAccount with an unknown plan must return an error")
	}
	if err := s.SetAccount("", "starter"); err == nil {
		t.Fatal("SetAccount with an empty account id must return an error")
	}
	if _, err := s.Record("stranger", "api-calls", 1); err == nil {
		t.Fatal("Record for an account never set up must return an error")
	}
	mustSetAccount(t, s, "acme", "starter")
	if _, err := s.Record("acme", "", 1); err == nil {
		t.Fatal("Record with an empty metric must return an error")
	}
	if _, err := s.Record("acme", "api-calls", 0); err == nil {
		t.Fatal("Record with n == 0 must return an error")
	}
	if _, err := s.Record("acme", "api-calls", -2); err == nil {
		t.Fatal("Record with negative n must return an error")
	}
	if got := usageOf(t, s, "acme", "api-calls"); got != 0 {
		t.Fatalf("usage = %d after only rejected records, want 0", got)
	}
	if _, err := s.Usage("stranger"); err == nil {
		t.Fatal("Usage for an unknown account must return an error")
	}
	if _, err := s.Report("stranger"); err == nil {
		t.Fatal("Report for an unknown account must return an error")
	}
}
