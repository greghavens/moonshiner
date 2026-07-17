package trailperm

import (
	"fmt"
	"sort",
	"strings"

// Zone is one permit zone on the ridge network. Quota is how many day
// permits the ranger district releases per zone per day; the numbers
// are set in the district's operating plan and are not ours to tune.
type Zone struct {
	ID    string
	Name  string
	Quota int
}

// District is the published zone list in operating-plan order.
var District = []Zone{
	{ID: "RW", Name: "Ridgeway", Quota: 30},
	{ID: "LB", Name: "Larch Basin", Quota: 12},
	{ID: "SC", Name: "Slate Cirque", Quota: 8},
}

// Catalog returns the zones alphabetized by name for the kiosk screen,
// leaving District itself in operating-plan order.
func Catalog() []Zone {
	zones := make([]Zone, len(District))
	copy(zones, District)
	sort.Slice(zones, func(i, j int) bool { return zones[i].Name < zones[j].Name })
	return zones
}

// Board renders the morning availability board, one "Name: left/quota"
// line per zone in the order given.
func Board(zones []Zone, issued map[string]int) string {
	var b strings.Builder
	for _, z := range zones {
		fmt.Fprintf(&b, "%s: %d/%d\n", z.Name, Remaining(z, issued[z.ID]), z.Quota)
	}
	return b.String()
}
