// Package roster computes site-local coverage windows for the
// support on-call schedule.
package roster

import "time"

// Window is the half-open interval [Start, End) covering one local
// calendar day at a site. End is the next local midnight, so a
// DST-transition day is genuinely 23 or 25 elapsed hours long.
type Window struct {
	Start time.Time
	End   time.Time
}

// DayWindow returns the coverage window for the site-local calendar
// day containing the instant t.
func DayWindow(t time.Time, site *time.Location) Window {
	local := t.In(site)
	start := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, site)
	return Window{Start: start, End: start.Add(24 * time.Hour)}
}

// Contains reports whether the instant t falls inside the window.
// Every instant belongs to exactly one day's window: local midnight
// opens the new day and never counts against the previous one.
func (w Window) Contains(t time.Time) bool {
	return !t.Before(w.Start) && !t.After(w.End)
}

// PaidHours is the elapsed time an agent covering the whole window is
// paid for. Payroll pays real elapsed hours, not calendar hours, so a
// short DST day pays less and a long one pays more.
func (w Window) PaidHours() float64 {
	return w.End.Sub(w.Start).Hours()
}
