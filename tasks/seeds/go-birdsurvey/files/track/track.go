// Package track turns recorded walk coordinates into shareable snippets.
package track

import (
	"fmt"
	"strings"

	"go-birdsurvey/track/internal/gpx"
)

// A Point is one recorded coordinate from a volunteer's GPS logger.
type Point struct {
	Lat float64
	Lon float64
}

// Snippet renders recorded points as waypoints named P1, P2, ...,
// one per line, ready to paste into the route editor.
func Snippet(pts []Point) string {
	lines := make([]string, len(pts))
	for i, p := range pts {
		lines[i] = gpx.Waypoint(p.Lat, p.Lon, fmt.Sprintf("P%d", i+1))
	}
	return strings.Join(lines, "\n")
}
