package bunkboard

import "testing"

func TestSummaryLineShowsCounts(t *testing.T) {
	got := SummaryLine(12, 16)
	want := "bunks filled: 12 of 16"
	if got != want {
		t.Fatalf("SummaryLine(12, 16) = %q, want %q", got, want)
	}
}

func TestRateLineShowsWholePercent(t *testing.T) {
	got := RateLine(12, 16)
	want := "occupancy 75%"
	if got != want {
		t.Fatalf("RateLine(12, 16) = %q, want %q", got, want)
	}
}

func TestGuestLineNamesGuestAndBunk(t *testing.T) {
	got := GuestLine("Lena", "A4")
	want := "Lena -> bunk A4"
	if got != want {
		t.Fatalf("GuestLine(Lena, A4) = %q, want %q", got, want)
	}
}

func TestBandLabelCoversAllBands(t *testing.T) {
	cases := []struct {
		pct  float64
		want string
	}{
		{95, "full"},
		{90, "full"},
		{75, "busy"},
		{60, "busy"},
		{59.9, "open"},
		{30, "open"},
		{0, "open"},
	}
	for _, c := range cases {
		if got := BandLabel(c.pct); got != c.want {
			t.Errorf("BandLabel(%v) = %q, want %q", c.pct, got, c.want)
		}
	}
}

func TestOccupancyAccumulatesCheckIns(t *testing.T) {
	var o Occupancy
	o.CheckIn(2)
	o.CheckIn(1)
	o.CheckIn(4)
	if got := o.Total(); got != 7 {
		t.Fatalf("Total() after check-ins of 2+1+4 = %d, want 7", got)
	}
}

func TestExportUsesDeskAppKeys(t *testing.T) {
	out, err := ExportRecords([]BunkRecord{{Bunk: "A4", Guest: "Lena"}})
	if err != nil {
		t.Fatalf("ExportRecords: %v", err)
	}
	want := `[{"bunk":"A4","guest":"Lena"}]`
	if out != want {
		t.Fatalf("ExportRecords empty-nights row = %s, want %s", out, want)
	}
}

func TestExportKeepsNightsWhenSet(t *testing.T) {
	out, err := ExportRecords([]BunkRecord{{Bunk: "B2", Guest: "Ola", Nights: 3}})
	if err != nil {
		t.Fatalf("ExportRecords: %v", err)
	}
	want := `[{"bunk":"B2","guest":"Ola","nights":3}]`
	if out != want {
		t.Fatalf("ExportRecords with nights = %s, want %s", out, want)
	}
}
