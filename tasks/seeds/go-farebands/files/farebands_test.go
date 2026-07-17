package farebands

import "testing"

func TestTariffTableAsPublished(t *testing.T) {
	if len(Bands) != 3 {
		t.Fatalf("tariff sheet has 3 bands, got %d", len(Bands))
	}
	want := []Band{
		{Name: "short", MaxKm: 3, Cents: 250},
		{Name: "city", MaxKm: 10, Cents: 375},
		{Name: "regional", MaxKm: 40, Cents: 620},
	}
	for i, w := range want {
		if Bands[i] != w {
			t.Errorf("band %d: got %+v, want %+v", i, Bands[i], w)
		}
	}
}

func TestFareWithinBands(t *testing.T) {
	cases := []struct{ km, cents int }{
		{0, 250}, {1, 250}, {3, 250},
		{4, 375}, {10, 375},
		{11, 620}, {40, 620},
	}
	for _, c := range cases {
		if got := Fare(c.km); got != c.cents {
			t.Errorf("Fare(%d) = %d, want %d", c.km, got, c.cents)
		}
	}
}

func TestFareLongHaulRule(t *testing.T) {
	cases := []struct{ km, cents int }{
		{41, 1240}, {80, 1240},
		{81, 1860}, {200, 1860},
	}
	for _, c := range cases {
		if got := Fare(c.km); got != c.cents {
			t.Errorf("Fare(%d) = %d, want %d", c.km, got, c.cents)
		}
	}
}

func TestLabelFormatsDollarsAndCents(t *testing.T) {
	cases := []struct {
		km   int
		want string
	}{
		{2, "2.50"}, {5, "3.75"}, {41, "12.40"},
	}
	for _, c := range cases {
		if got := Label(c.km); got != c.want {
			t.Errorf("Label(%d) = %q, want %q", c.km, got, c.want)
		}
	}
}

func TestBandForPicksTicketStock(t *testing.T) {
	cases := []struct {
		km   int
		want string
	}{
		{1, "short"}, {7, "city"}, {40, "regional"}, {50, "extended"},
	}
	for _, c := range cases {
		if got := BandFor(c.km); got != c.want {
			t.Errorf("BandFor(%d) = %q, want %q", c.km, got, c.want)
		}
	}
}
