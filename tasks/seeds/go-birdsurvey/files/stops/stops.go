// Package stops holds the fixed observation points of a survey route.
package stops

// A Stop is one observation point volunteers count from, with the
// standard count duration the protocol assigns it.
type Stop struct {
	Code    string
	Name    string
	Minutes int
}

// TotalMinutes is the on-station time for a set of stops.
func TotalMinutes(ss []Stop) int {
	total := 0
	for _, s := range ss {
		total += s.Minutes
	}
	return total
}
