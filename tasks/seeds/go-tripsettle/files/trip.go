// Package tripsettle prices the nightly car-share export into member
// statements for the billing hand-off.
package tripsettle

import (
	"fmt"
	"strconv"
	"strings"
)

// Trip is one completed ride from the nightly export.
type Trip struct {
	ID    string
	Class string
	Km    int
	Min   int
}

func parseTrip(ln string) (Trip, error) {
	parts := strings.Split(ln, ",")
	if len(parts) != 4 {
		return Trip{}, fmt.Errorf("expected 4 fields, got %d", len(parts))
	}
	km, err := strconv.Atoi(strings.TrimSpace(parts[2]))
	if err != nil {
		return Trip{}, fmt.Errorf("bad km field: %v", err)
	}
	min, err := strconv.Atoi(strings.TrimSpace(parts[3]))
	if err != nil {
		return Trip{}, fmt.Errorf("bad minutes field: %v", err)
	}
	return Trip{
		ID:    strings.TrimSpace(parts[0]),
		Class: strings.TrimSpace(parts[1]),
		Km:    km,
		Min:   min,
	}, nil
}

func validateTrip(t Trip) error {
	if t.ID == "" {
		return fmt.Errorf("missing trip id")
	}
	if t.Km < 0 {
		return fmt.Errorf("trip %s: negative distance", t.ID)
	}
	if t.Min < 0 {
		return fmt.Errorf("trip %s: negative duration", t.ID)
	}
	return nil
}

// LoadTrips parses the nightly export. A row that does not parse aborts
// the load; a row that parses but fails validation is skipped, and the
// first such problem is reported so the export job can flag the file.
func LoadTrips(text string) ([]Trip, error) {
    var trips []Trip
    var firstErr error
    for i, ln := range strings.Split(text, "\n") {
        ln = strings.TrimSpace(ln)
        if ln == "" {
            continue
        }
        t, err := parseTrip(ln)
        if err != nil {
            return nil, fmt.Errorf("line %d: %w", i+1, err)
        }
        if firstErr := validateTrip(t); firstErr != nil {
            continue
        }
        trips = append(trips, t)
    }
    return trips, firstErr
}
