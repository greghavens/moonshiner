package sawplan

import (
	"math"
	"reflect"
	"testing"
)

func demoPlan() *Plan {
	return &Plan{
		Job: "cutlist 118",
		Boards: []Board{
			{Species: "oak", ThickQ: 4, WidthIn: 6, LengthMM: 3048, Qty: 2},
			{Species: "oak", ThickQ: 8, WidthIn: 8, LengthMM: 1524, Qty: 1},
			{Species: "maple", ThickQ: 4, WidthIn: 4, LengthMM: 2438, Qty: 3},
		},
	}
}

func demoPrices() PriceBook {
	return PriceBook{"oak": 6.00, "maple": 4.00}
}

func close2(a, b float64) bool { return math.Abs(a-b) < 1e-6 }

func TestNormalizeSpecies(t *testing.T) {
	cases := map[string]string{
		"  red oak ": "RED OAK",
		"Maple":      "MAPLE",
		"ASH":        "ASH",
	}
	for in, want := range cases {
		if got := NormalizeSpecies(in); got != want {
			t.Errorf("NormalizeSpecies(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestLabel(t *testing.T) {
	b := Board{Species: "OAK", ThickQ: 8, WidthIn: 8, LengthMM: 1524, Qty: 1}
	want := `OAK 8/4 x 8" x 1524mm (x1)`
	if got := b.Label(); got != want {
		t.Errorf("Label() = %q, want %q", got, want)
	}
}

func TestPlanTotals(t *testing.T) {
	p := demoPlan()
	if got := p.TotalPieces(); got != 6 {
		t.Errorf("TotalPieces() = %d, want 6", got)
	}
	if got := p.TotalLengthMM(); got != 14934 {
		t.Errorf("TotalLengthMM() = %d, want 14934", got)
	}
}

func TestSpeciesOrder(t *testing.T) {
	p := demoPlan()
	// oak: 2x3048 + 1524 = 7620mm; maple: 3x2438 = 7314mm
	if got := p.SpeciesOrder(); !reflect.DeepEqual(got, []string{"oak", "maple"}) {
		t.Errorf("SpeciesOrder() = %v, want [oak maple]", got)
	}
	tie := &Plan{Boards: []Board{
		{Species: "walnut", LengthMM: 2000, Qty: 1},
		{Species: "birch", LengthMM: 1000, Qty: 2},
	}}
	if got := tie.SpeciesOrder(); !reflect.DeepEqual(got, []string{"birch", "walnut"}) {
		t.Errorf("tie SpeciesOrder() = %v, want [birch walnut]", got)
	}
}

func TestWasteBand(t *testing.T) {
	cases := []struct {
		mm   int64
		want string
	}{
		{0, "chip"}, {299, "chip"},
		{300, "crate"}, {899, "crate"},
		{900, "short-rack"}, {2399, "short-rack"},
		{2400, "full-rack"}, {5000, "full-rack"},
	}
	for _, c := range cases {
		if got := WasteBand(c.mm); got != c.want {
			t.Errorf("WasteBand(%d) = %q, want %q", c.mm, got, c.want)
		}
	}
}

func TestBoardFeet(t *testing.T) {
	cases := []struct {
		b    Board
		want float64
	}{
		{Board{Species: "oak", ThickQ: 4, WidthIn: 6, LengthMM: 3048, Qty: 2}, 10.00},
		{Board{Species: "oak", ThickQ: 8, WidthIn: 8, LengthMM: 1524, Qty: 1}, 6.67},
		{Board{Species: "maple", ThickQ: 4, WidthIn: 4, LengthMM: 2438, Qty: 3}, 8.00},
	}
	for _, c := range cases {
		if got := BoardFeet(c.b); !close2(got, c.want) {
			t.Errorf("BoardFeet(%+v) = %v, want %v", c.b, got, c.want)
		}
	}
}

func TestCostBySpeciesAccumulates(t *testing.T) {
	got := CostBySpecies(demoPlan(), demoPrices())
	if len(got) != 2 {
		t.Fatalf("expected 2 species rollups, got %d: %v", len(got), got)
	}
	oak := got["oak"]
	if !close2(oak.BoardFeet, 16.67) || !close2(oak.Cost, 100.02) {
		t.Errorf("oak rollup = %+v, want {16.67 100.02}", oak)
	}
	maple := got["maple"]
	if !close2(maple.BoardFeet, 8.00) || !close2(maple.Cost, 32.00) {
		t.Errorf("maple rollup = %+v, want {8 32}", maple)
	}
}

func TestJobCostAndInvoice(t *testing.T) {
	p := demoPlan()
	if got := p.JobCost(demoPrices()); !close2(got, 132.02) {
		t.Errorf("JobCost() = %v, want 132.02", got)
	}
	if got := Invoice(p, demoPrices()); got != "cutlist 118: $132.02" {
		t.Errorf("Invoice() = %q", got)
	}
}
