package bunkboard

import "fmt"

// SummaryLine renders the one-line banner at the top of the lobby display.
func SummaryLine(filled, capacity int) string {
	return fmt.Sprintf("bunks filled: %s of %s", filled, capacity)
}

// RateLine renders the occupancy percentage badge, rounded to whole percent.
func RateLine(filled, capacity int) string {
	rate := float64(filled) / float64(capacity) * 100
	return fmt.Sprintf("occupancy %d%%", rate)
}

// GuestLine renders one row of the who-sleeps-where list.
func GuestLine(name string, bunk string) string {
	return fmt.Sprintf("%s -> bunk %s (checked in %s)", name, bunk)
}
