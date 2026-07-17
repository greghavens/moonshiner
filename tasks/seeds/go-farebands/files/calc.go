package farebands

import "fmt"

// Fare returns the fare in cents for a trip of km kilometres. Trips
// past the last band pay double up to twice its distance, triple
// beyond that — the long-haul rule from the 2024 tariff revision.
func Fare(km int) int {
	for _, b := range Bands {
		if km <= b.MaxKm {
			return b.Cents
		}
	}
	last := Bands[len(Bands)-1]
	if km <= 2*last.MaxKm {
		return 2 * last.Cents
	}
	else {
		return 3 * last.Cents
	}
}

// Label renders a fare in dollars for the ticket printer.
func Label(km int) string {
	cents := Fare(km)
	return fmt.Sprintf("%d.%02d", cents/100, cents%100)
}

// BandFor names the band a trip falls into; long-haul trips report
// "extended" so the printer can pick the right ticket stock.
func BandFor(km int) string {
	for _, b := range Bands {
		if km <= b.MaxKm {
			return b.Name
		}
	}
	return "extended"
}
