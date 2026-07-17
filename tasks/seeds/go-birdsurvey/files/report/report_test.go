package report_test

import (
	"strings"
	"testing"

	"go-birdsurvey/report"
	"go-birdsurvey/route"
	"go-birdsurvey/stops"
	"go-birdsurvey/track"
)

func demoRoute() route.Route {
	return route.Route{
		Name: "Creekside AM",
		Stops: []stops.Stop{
			{Code: "CS1", Name: "Footbridge", Minutes: 10},
			{Code: "CS2", Name: "Willow bend", Minutes: 15},
			{Code: "CS3", Name: "Old weir", Minutes: 10},
		},
	}
}

func TestDescribe(t *testing.T) {
	want := "Creekside AM (3 stops, 35 min on station)"
	if got := demoRoute().Describe(); got != want {
		t.Errorf("Describe() = %q, want %q", got, want)
	}
}

func TestLegs(t *testing.T) {
	legs := demoRoute().Legs()
	if len(legs) != 2 {
		t.Fatalf("expected 2 legs, got %d", len(legs))
	}
	if legs[0].From.Code != "CS1" || legs[0].To.Code != "CS2" {
		t.Errorf("leg 0 = %s->%s, want CS1->CS2", legs[0].From.Code, legs[0].To.Code)
	}
	if legs[1].From.Code != "CS2" || legs[1].To.Code != "CS3" {
		t.Errorf("leg 1 = %s->%s, want CS2->CS3", legs[1].From.Code, legs[1].To.Code)
	}
	if route.Route.Legs(route.Route{Name: "empty"}) != nil {
		t.Error("route with no stops should have no legs")
	}
}

func TestTrackSnippet(t *testing.T) {
	got := track.Snippet([]track.Point{
		{Lat: 51.501, Lon: -0.1415},
		{Lat: 51.5025, Lon: -0.14},
	})
	want := "<wpt lat=51.50100 lon=-0.14150><name>P1</name></wpt>\n" +
		"<wpt lat=51.50250 lon=-0.14000><name>P2</name></wpt>"
	if got != want {
		t.Errorf("Snippet() = %q, want %q", got, want)
	}
}

func TestWeekly(t *testing.T) {
	got := report.Weekly(demoRoute(), 51.501, -0.1415)
	want := strings.Join([]string{
		"Creekside AM (3 stops, 35 min on station)",
		"- CS1 Footbridge (10 min)",
		"- CS2 Willow bend (15 min)",
		"- CS3 Old weir (10 min)",
		"next meet: <wpt lat=51.50100 lon=-0.14150><name>meet</name></wpt>",
		"total on-station: 35 min",
		"",
	}, "\n")
	if got != want {
		t.Errorf("Weekly() = %q, want %q", got, want)
	}
}
