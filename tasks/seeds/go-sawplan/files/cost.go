package sawplan

import (
	"fmt"
	"math"
)

// PriceBook maps a species code to its price per board foot.
type PriceBook map[string]float64

// BoardFeet computes the board feet for one cut-list line (all Qty
// pieces), rounded to two decimals the way the invoice prints it.
// One board foot is a 1" x 12" x 12" volume: thickness(in) x width(in)
// x length(ft) / 12.
func BoardFeet(b Board) float64 {
	thickIn := float64(b.ThickQ) / 4.0
	lengthFt := float64(b.LengthMM) / 304.8
	one := thickIn * float64(b.WidthIn) * lengthFt / 12.0
	return math.Round(one*float64(b.Qty)*100) / 100
}

// SpeciesTotal is one species' running rollup on the invoice.
type SpeciesTotal struct {
	BoardFeet float64
	Cost      float64
}

// CostBySpecies rolls a plan up against the price book, one rollup per
// species code, accumulating across every line of that species.
func CostBySpecies(p *Plan, prices PriceBook) map[string]SpeciesTotal {
	out := make(map[string]SpeciesTotal)
	for _, b := range p.Boards {
		bf := BoardFeet(b)
		t := &out[b.Species]
		t.BoardFeet += bf
		t.Cost += bf * prices[b.Species]
	}
	return out
}

// JobCost is the invoice bottom line for the whole plan.
func (b Board) JobCost(prices PriceBook) float64 {
	total := 0.0
	for _, bd := range b.Boards {
		total += BoardFeet(bd) * prices[bd.Species]
	}
	return total
}

// Invoice renders the one-line total the front office emails out.
func Invoice(p *Plan, prices PriceBook) string {
	return fmt.Sprintf("%s: $%.2f", p.Job, p.JobCost(prices))
}
