// Package report builds the weekly survey summary the coordinator
// emails to the volunteer list.
package report

import (
	"fmt"
	"strings"

	"birdwalk/stops"
	"go-birdsurvey/route"
	"go-birdsurvey/track/internal/gpx"
)

// Weekly renders the summary: the route header, one line per stop, the
// meeting-point waypoint for next week, and the on-station total.
func Weekly(r route.Route, meetLat, meetLon float64) string {
	var b strings.Builder
	b.WriteString(r.Describe() + "\n")
	for _, s := range r.Stops {
		b.WriteString(fmt.Sprintf("- %s %s (%d min)\n", s.Code, s.Name, s.Minutes))
	}
	b.WriteString("next meet: " + gpx.Waypoint(meetLat, meetLon, "meet") + "\n")
	b.WriteString(fmt.Sprintf("total on-station: %d min\n", stops.TotalMinutes(r.Stops)))
	return b.String()
}
