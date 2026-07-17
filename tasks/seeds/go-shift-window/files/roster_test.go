package roster

import (
	"testing"
	"time"
	_ "time/tzdata"
)

func nyc(t *testing.T) *time.Location {
	t.Helper()
	loc, err := time.LoadLocation("America/New_York")
	if err != nil {
		t.Fatalf("load zone: %v", err)
	}
	return loc
}

func TestOrdinaryDayWindow(t *testing.T) {
	loc := nyc(t)
	w := DayWindow(time.Date(2026, 7, 14, 9, 30, 0, 0, loc), loc)
	if want := time.Date(2026, 7, 14, 0, 0, 0, 0, loc); !w.Start.Equal(want) {
		t.Fatalf("start = %v, want %v", w.Start, want)
	}
	if want := time.Date(2026, 7, 15, 0, 0, 0, 0, loc); !w.End.Equal(want) {
		t.Fatalf("end = %v, want next local midnight %v", w.End, want)
	}
	if got := w.PaidHours(); got != 24.0 {
		t.Fatalf("paid hours = %v, want 24 on an ordinary day", got)
	}
}

func TestSpringForwardDayIsShort(t *testing.T) {
	loc := nyc(t)
	w := DayWindow(time.Date(2026, 3, 8, 12, 0, 0, 0, loc), loc)
	if want := time.Date(2026, 3, 9, 0, 0, 0, 0, loc); !w.End.Equal(want) {
		t.Fatalf("end = %v, want next local midnight %v", w.End, want)
	}
	if got := w.PaidHours(); got != 23.0 {
		t.Fatalf("paid hours = %v, want 23 elapsed hours on the spring-forward day", got)
	}
	earlyNextDay := time.Date(2026, 3, 9, 0, 30, 0, 0, loc)
	if w.Contains(earlyNextDay) {
		t.Fatalf("%v belongs to March 9, not the March 8 window", earlyNextDay)
	}
}

func TestFallBackDayIsLong(t *testing.T) {
	loc := nyc(t)
	w := DayWindow(time.Date(2026, 11, 1, 12, 0, 0, 0, loc), loc)
	if want := time.Date(2026, 11, 2, 0, 0, 0, 0, loc); !w.End.Equal(want) {
		t.Fatalf("end = %v, want next local midnight %v", w.End, want)
	}
	if got := w.PaidHours(); got != 25.0 {
		t.Fatalf("paid hours = %v, want 25 elapsed hours on the fall-back day", got)
	}
	lateEvening := time.Date(2026, 11, 1, 23, 30, 0, 0, loc)
	if !w.Contains(lateEvening) {
		t.Fatalf("%v is still November 1 and must be inside the window", lateEvening)
	}
}

func TestFoldHourInstantsBothBelongToTheDay(t *testing.T) {
	loc := nyc(t)
	w := DayWindow(time.Date(2026, 11, 1, 12, 0, 0, 0, loc), loc)
	firstOneThirty := time.Date(2026, 11, 1, 5, 30, 0, 0, time.UTC)  // 1:30 EDT
	secondOneThirty := time.Date(2026, 11, 1, 6, 30, 0, 0, time.UTC) // 1:30 EST
	if !w.Contains(firstOneThirty) || !w.Contains(secondOneThirty) {
		t.Fatalf("both 1:30 AM instants of the fold must be inside the window")
	}
}

func TestMidnightBoundariesAreHalfOpen(t *testing.T) {
	loc := nyc(t)
	w := DayWindow(time.Date(2026, 7, 14, 12, 0, 0, 0, loc), loc)
	if !w.Contains(w.Start) {
		t.Fatal("local midnight opens the day and must be inside its own window")
	}
	lastMoment := time.Date(2026, 7, 14, 23, 59, 59, 0, loc)
	if !w.Contains(lastMoment) {
		t.Fatalf("%v must be inside the window", lastMoment)
	}
	nextMidnight := time.Date(2026, 7, 15, 0, 0, 0, 0, loc)
	if w.Contains(nextMidnight) {
		t.Fatalf("%v opens July 15 and must not count against July 14", nextMidnight)
	}
}

func TestUTCInstantMapsToLocalDay(t *testing.T) {
	loc := nyc(t)
	// 2026-03-09T02:30Z is 22:30 EDT on March 8.
	w := DayWindow(time.Date(2026, 3, 9, 2, 30, 0, 0, time.UTC), loc)
	if want := time.Date(2026, 3, 8, 0, 0, 0, 0, loc); !w.Start.Equal(want) {
		t.Fatalf("start = %v, want %v — the window must follow the site's local day", w.Start, want)
	}
}
