package snapprune

import (
	"reflect"
	"testing"
	"time"
)

// Acceptance contract for grandfather-father-son retention: PlanGFS,
// the dry-run Plan.Render output, and the last-snapshot guard.

func TestPlanGFSKeepsDailyWeeklyMonthly(t *testing.T) {
	// Sat 2026-07-11. Daily window: Jul 11, 10, 9. Weekly window: ISO
	// weeks Jul 6-12 and Jun 29-Jul 5. Monthly window: 2026-07, 2026-06.
	now := time.Date(2026, 7, 11, 12, 0, 0, 0, time.UTC)
	names := []string{
		"db-20260615-093000", // June, but not June's newest -> delete
		"db-20260711-060000", // today, but not today's newest -> delete
		"db-20260703-040000", // newest of ISO week Jun 29-Jul 5 -> weekly
		"db-20260530-010000", // May: outside every window -> delete
		"db-20260711-120000", // newest of Jul 11 -> daily
		"db-20260701-000000", // same ISO week as Jul 3, older -> delete
		"db-20260620-093000", // newest June snapshot -> monthly
		"db-20260710-050000", // newest (only) of Jul 10 -> daily
		"db-20260708-050000", // current ISO week, but week's newest is already kept -> delete
	}
	plan, err := PlanGFS(names, Policy{Daily: 3, Weekly: 2, Monthly: 2}, now)
	if err != nil {
		t.Fatal(err)
	}
	wantKeep := []Decision{
		{Name: "db-20260711-120000", Reason: "daily"},
		{Name: "db-20260710-050000", Reason: "daily"},
		{Name: "db-20260703-040000", Reason: "weekly"},
		{Name: "db-20260620-093000", Reason: "monthly"},
	}
	wantDelete := []string{
		"db-20260711-060000",
		"db-20260708-050000",
		"db-20260701-000000",
		"db-20260615-093000",
		"db-20260530-010000",
	}
	if !reflect.DeepEqual(plan.Keep, wantKeep) {
		t.Fatalf("Keep = %+v,\nwant %+v", plan.Keep, wantKeep)
	}
	if !reflect.DeepEqual(plan.Delete, wantDelete) {
		t.Fatalf("Delete = %v,\nwant %v", plan.Delete, wantDelete)
	}
}

func TestPlanGFSDailyOnly(t *testing.T) {
	now := time.Date(2026, 7, 11, 23, 0, 0, 0, time.UTC)
	names := []string{
		"app-20260709-220000",
		"app-20260710-220000",
		"app-20260711-063000",
		"app-20260711-180000",
	}
	plan, err := PlanGFS(names, Policy{Daily: 2}, now)
	if err != nil {
		t.Fatal(err)
	}
	wantKeep := []Decision{
		{Name: "app-20260711-180000", Reason: "daily"},
		{Name: "app-20260710-220000", Reason: "daily"},
	}
	wantDelete := []string{"app-20260711-063000", "app-20260709-220000"}
	if !reflect.DeepEqual(plan.Keep, wantKeep) {
		t.Fatalf("Keep = %+v, want %+v", plan.Keep, wantKeep)
	}
	if !reflect.DeepEqual(plan.Delete, wantDelete) {
		t.Fatalf("Delete = %v, want %v", plan.Delete, wantDelete)
	}
}

func TestPlanGFSMonthlyWindowCrossesYearBoundary(t *testing.T) {
	now := time.Date(2026, 1, 15, 8, 0, 0, 0, time.UTC)
	names := []string{
		"db-20251020-030000", // October: outside Monthly=3 window
		"db-20251130-030000",
		"db-20251201-030000",
		"db-20251228-030000",
		"db-20260110-030000",
	}
	plan, err := PlanGFS(names, Policy{Monthly: 3}, now)
	if err != nil {
		t.Fatal(err)
	}
	wantKeep := []Decision{
		{Name: "db-20260110-030000", Reason: "monthly"},
		{Name: "db-20251228-030000", Reason: "monthly"},
		{Name: "db-20251130-030000", Reason: "monthly"},
	}
	wantDelete := []string{"db-20251201-030000", "db-20251020-030000"}
	if !reflect.DeepEqual(plan.Keep, wantKeep) {
		t.Fatalf("Keep = %+v, want %+v", plan.Keep, wantKeep)
	}
	if !reflect.DeepEqual(plan.Delete, wantDelete) {
		t.Fatalf("Delete = %v, want %v", plan.Delete, wantDelete)
	}
}

func TestPlanGFSGuardNeverDeletesTheLastSnapshot(t *testing.T) {
	now := time.Date(2026, 7, 11, 12, 0, 0, 0, time.UTC)

	// Everything is far older than every window; without the guard the
	// plan would delete the whole bucket.
	plan, err := PlanGFS([]string{"db-20240101-000000", "db-20240102-000000"},
		Policy{Daily: 7, Weekly: 4, Monthly: 3}, now)
	if err != nil {
		t.Fatal(err)
	}
	wantKeep := []Decision{{Name: "db-20240102-000000", Reason: "guard"}}
	if !reflect.DeepEqual(plan.Keep, wantKeep) {
		t.Fatalf("Keep = %+v, want %+v", plan.Keep, wantKeep)
	}
	if !reflect.DeepEqual(plan.Delete, []string{"db-20240101-000000"}) {
		t.Fatalf("Delete = %v", plan.Delete)
	}

	// A single ancient snapshot must survive with an empty delete list.
	plan, err = PlanGFS([]string{"db-20240101-000000"}, Policy{Daily: 1}, now)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(plan.Keep, []Decision{{Name: "db-20240101-000000", Reason: "guard"}}) {
		t.Fatalf("Keep = %+v", plan.Keep)
	}
	if len(plan.Delete) != 0 {
		t.Fatalf("Delete = %v, want empty", plan.Delete)
	}

	// Guard ties resolve to the name-ascending winner, same as sorting.
	plan, err = PlanGFS([]string{"beta-20240101-000000", "alpha-20240101-000000"}, Policy{}, now)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(plan.Keep, []Decision{{Name: "alpha-20240101-000000", Reason: "guard"}}) {
		t.Fatalf("Keep = %+v", plan.Keep)
	}
}

func TestPlanGFSEmptyInputAndErrors(t *testing.T) {
	now := time.Date(2026, 7, 11, 12, 0, 0, 0, time.UTC)
	plan, err := PlanGFS(nil, Policy{Daily: 7}, now)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Keep) != 0 || len(plan.Delete) != 0 {
		t.Fatalf("plan for no snapshots = %+v, want empty", plan)
	}
	if _, err := PlanGFS([]string{"not-a-snapshot"}, Policy{Daily: 7}, now); err == nil {
		t.Fatal("PlanGFS with malformed name succeeded, want error")
	}
}

func TestRenderDryRunOutput(t *testing.T) {
	plan := Plan{
		Keep: []Decision{
			{Name: "db-20260711-120000", Reason: "daily"},
			{Name: "db-20260620-093000", Reason: "monthly"},
		},
		Delete: []string{"db-20260530-010000"},
	}
	want := "keep db-20260711-120000 (daily)\n" +
		"keep db-20260620-093000 (monthly)\n" +
		"delete db-20260530-010000\n"
	if got := plan.Render(); got != want {
		t.Fatalf("Render() = %q, want %q", got, want)
	}
	if got := (Plan{}).Render(); got != "" {
		t.Fatalf("empty Plan Render() = %q, want empty string", got)
	}
}
