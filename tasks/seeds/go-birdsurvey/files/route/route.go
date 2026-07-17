// Package route assembles ordered survey routes out of stops.
package route

import (
	"fmt"

	"birdwalk/stops"
)

// A Route is the ordered walk a volunteer team covers in one morning.
type Route struct {
	Name  string
	Stops []stops.Stop
}

// Describe renders the route header line for the clipboard sheets.
func (r Route) Describe() string {
	return fmt.Sprintf("%s (%d stops, %d min on station)",
		r.Name, len(r.Stops), stops.TotalMinutes(r.Stops))
}
