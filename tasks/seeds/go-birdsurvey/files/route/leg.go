package route

import "birdwalk/stops"

// A Leg is the walk between two consecutive stops on a route.
type Leg struct {
	From stops.Stop
	To   stops.Stop
}

// Legs pairs consecutive stops into the walking legs of the route.
func (r Route) Legs() []Leg {
	if len(r.Stops) < 2 {
		return nil
	}
	legs := make([]Leg, 0, len(r.Stops)-1)
	for i := 1; i < len(r.Stops); i++ {
		legs = append(legs, Leg{From: r.Stops[i-1], To: r.Stops[i]})
	}
	return legs
}
