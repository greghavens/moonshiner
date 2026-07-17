package cronsched

import (
	"testing"
	"time"
)

func at(y int, mo time.Month, d, h, mi, s int) time.Time {
	return time.Date(y, mo, d, h, mi, s, 0, time.UTC)
}

func mustParse(t *testing.T, expr string) *Schedule {
	t.Helper()
	s, err := Parse(expr)
	if err != nil {
		t.Fatalf("Parse(%q): %v", expr, err)
	}
	return s
}

func expectNext(t *testing.T, expr string, after, want time.Time) {
	t.Helper()
	got := mustParse(t, expr).Next(after)
	if !got.Equal(want) {
		t.Fatalf("Next(%q, %s) = %s, want %s", expr, after, got, want)
	}
}

func TestParseRejectsInvalidExpressions(t *testing.T) {
	bad := []string{
		"",
		"* * * *",
		"* * * * * *",
		"60 * * * *",
		"* 24 * * *",
		"* * 0 * *",
		"* * 32 * *",
		"* * * 0 *",
		"* * * 13 *",
		"* * * * 8",
		"*/0 * * * *",
		"10-5 * * * *",
		"1,,2 * * * *",
		"a * * * *",
		"1-5/x * * * *",
		"5/2 * * * *",
		"0 0 * * MON",
		"30 3 * * SUN",
		"-5 * * * *",
		"5- * * * *",
	}
	for _, expr := range bad {
		if _, err := Parse(expr); err == nil {
			t.Errorf("Parse(%q) accepted, want error", expr)
		}
	}
}

func TestParseAcceptsFlexibleWhitespace(t *testing.T) {
	s, err := Parse("  */15\t8-10  * *   1-5  ")
	if err != nil {
		t.Fatalf("whitespace-separated fields must parse: %v", err)
	}
	// Mon 2026-03-09 09:30 matches (minute 30, hour 9, weekday Monday).
	if !s.Matches(at(2026, time.March, 9, 9, 30, 0)) {
		t.Fatal("expression did not match a time it clearly covers")
	}
}

func TestMatchesIgnoresSecondsAndNanos(t *testing.T) {
	s := mustParse(t, "*/15 * * * *")
	tt := time.Date(2026, time.March, 10, 12, 15, 42, 999_000_000, time.UTC)
	if !s.Matches(tt) {
		t.Fatal("Matches must operate at minute resolution (seconds ignored)")
	}
	if s.Matches(at(2026, time.March, 10, 12, 14, 0)) {
		t.Fatal("12:14 must not match */15")
	}
}

func TestNextIsStrictlyAfter(t *testing.T) {
	expectNext(t, "*/15 * * * *", at(2026, time.March, 10, 12, 7, 30), at(2026, time.March, 10, 12, 15, 0))
	// Landing exactly on a matching minute still moves forward.
	expectNext(t, "*/15 * * * *", at(2026, time.March, 10, 12, 15, 0), at(2026, time.March, 10, 12, 30, 0))
	// Every-minute schedule from a mid-minute instant: seconds get zeroed.
	expectNext(t, "* * * * *", at(2026, time.March, 10, 12, 30, 45), at(2026, time.March, 10, 12, 31, 0))
}

func TestListsRangesAndSteps(t *testing.T) {
	// minutes {5,10,15,20,45}, hours {8,9,10}
	expr := "5,10-20/5,45 8-10 * * *"
	expectNext(t, expr, at(2026, time.March, 10, 9, 16, 0), at(2026, time.March, 10, 9, 20, 0))
	expectNext(t, expr, at(2026, time.March, 10, 9, 45, 0), at(2026, time.March, 10, 10, 5, 0))
	expectNext(t, expr, at(2026, time.March, 10, 10, 45, 0), at(2026, time.March, 11, 8, 5, 0))

	// step over a range: 10-30/7 -> {10,17,24}
	stepped := mustParse(t, "10-30/7 * * * *")
	wantMinutes := map[int]bool{10: true, 17: true, 24: true}
	for m := 0; m < 60; m++ {
		got := stepped.Matches(at(2026, time.June, 1, 12, m, 0))
		if got != wantMinutes[m] {
			t.Fatalf("minute %d: Matches=%v, want %v (10-30/7 must hit 10,17,24 only)", m, got, wantMinutes[m])
		}
	}

	// */10 on hours -> 0,10,20
	hourly := mustParse(t, "0 */10 * * *")
	for h := 0; h < 24; h++ {
		want := h%10 == 0
		if got := hourly.Matches(at(2026, time.June, 1, h, 0, 0)); got != want {
			t.Fatalf("hour %d: Matches=%v, want %v", h, got, want)
		}
	}
}

func TestDayOfWeekIncludingSevenAsSunday(t *testing.T) {
	// 2026-03-13 is a Friday; 15th is a Sunday; 16th is a Monday.
	expectNext(t, "30 9 * * 1", at(2026, time.March, 11, 10, 0, 0), at(2026, time.March, 16, 9, 30, 0))
	expectNext(t, "0 12 * * 7", at(2026, time.March, 13, 0, 0, 0), at(2026, time.March, 15, 12, 0, 0))
	expectNext(t, "0 12 * * 0", at(2026, time.March, 13, 0, 0, 0), at(2026, time.March, 15, 12, 0, 0))
}

func TestDomDowEitherOrRule(t *testing.T) {
	// Both day fields restricted: fire on the 13th OR on Friday.
	expr := "0 0 13 * 5"
	expectNext(t, expr, at(2026, time.February, 1, 0, 0, 0), at(2026, time.February, 6, 0, 0, 0))   // first Friday
	expectNext(t, expr, at(2026, time.February, 6, 0, 0, 0), at(2026, time.February, 13, 0, 0, 0))  // Friday the 13th
	expectNext(t, expr, at(2026, time.February, 13, 0, 0, 0), at(2026, time.February, 20, 0, 0, 0)) // next Friday

	// Only dom restricted: plain AND with the wildcard dow.
	expectNext(t, "0 0 13 * *", at(2026, time.February, 7, 0, 0, 0), at(2026, time.February, 13, 0, 0, 0))
	// Only dow restricted.
	expectNext(t, "0 0 * * 5", at(2026, time.February, 7, 0, 0, 0), at(2026, time.February, 13, 0, 0, 0))

	// */1 spans every day but is still a restricted field, so it ORs with
	// the weekday: the schedule fires every midnight, not just Fridays.
	expectNext(t, "0 0 */1 * 5", at(2026, time.February, 7, 0, 0, 0), at(2026, time.February, 8, 0, 0, 0))
}

func TestMonthBoundariesAndLeapYears(t *testing.T) {
	// April has no 31st: skip straight to May 31.
	expectNext(t, "0 0 31 * *", at(2026, time.April, 1, 0, 0, 0), at(2026, time.May, 31, 0, 0, 0))
	// Next Feb 29 after March 2026 is in 2028.
	expectNext(t, "0 0 29 2 *", at(2026, time.March, 1, 0, 0, 0), at(2028, time.February, 29, 0, 0, 0))
	// Rolling over a year boundary.
	expectNext(t, "0 0 1 1 *", at(2026, time.March, 1, 0, 0, 0), at(2027, time.January, 1, 0, 0, 0))
}

func TestImpossibleDateReturnsZeroQuickly(t *testing.T) {
	s := mustParse(t, "0 0 30 2 *")
	start := time.Now()
	got := s.Next(at(2026, time.January, 1, 0, 0, 0))
	elapsed := time.Since(start)
	if !got.IsZero() {
		t.Fatalf("Feb 30 never exists; Next = %s, want the zero time", got)
	}
	if elapsed > 10*time.Second {
		t.Fatalf("Next took %s giving up on an impossible date — skip dead days/months instead of stepping minute by minute", elapsed)
	}
}

func TestNextNChainsStrictlyForward(t *testing.T) {
	s := mustParse(t, "*/15 * * * *")
	got := s.NextN(at(2026, time.March, 10, 12, 7, 0), 4)
	want := []time.Time{
		at(2026, time.March, 10, 12, 15, 0),
		at(2026, time.March, 10, 12, 30, 0),
		at(2026, time.March, 10, 12, 45, 0),
		at(2026, time.March, 10, 13, 0, 0),
	}
	if len(got) != len(want) {
		t.Fatalf("NextN returned %d times, want %d: %v", len(got), len(want), got)
	}
	for i := range want {
		if !got[i].Equal(want[i]) {
			t.Fatalf("NextN[%d] = %s, want %s", i, got[i], want[i])
		}
	}
}

func TestNextNNonPositiveIsEmpty(t *testing.T) {
	s := mustParse(t, "* * * * *")
	if got := s.NextN(at(2026, time.March, 1, 0, 0, 0), 0); len(got) != 0 {
		t.Fatalf("NextN(_, 0) = %v, want empty", got)
	}
	if got := s.NextN(at(2026, time.March, 1, 0, 0, 0), -3); len(got) != 0 {
		t.Fatalf("NextN(_, -3) = %v, want empty", got)
	}
}

func TestNextPreservesLocation(t *testing.T) {
	loc := time.FixedZone("UTC+1", 3600)
	after := time.Date(2026, time.March, 10, 12, 7, 0, 0, loc)
	got := mustParse(t, "*/15 * * * *").Next(after)
	const wantWall = "2026-03-10T12:15:00+01:00"
	if got.Format(time.RFC3339) != wantWall {
		t.Fatalf("Next in a fixed zone = %s, want %s (schedule fields are wall-clock in after's location)", got.Format(time.RFC3339), wantWall)
	}
}

func TestNextFarFutureStaysCorrectAcrossMonths(t *testing.T) {
	// 23:59 on the last day of each month in {Jan,Mar} only.
	expr := "59 23 31 1,3 *"
	expectNext(t, expr, at(2026, time.February, 1, 0, 0, 0), at(2026, time.March, 31, 23, 59, 0))
	expectNext(t, expr, at(2026, time.April, 1, 0, 0, 0), at(2027, time.January, 31, 23, 59, 0))
}
