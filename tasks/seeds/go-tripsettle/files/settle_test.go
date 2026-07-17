package tripsettle

import (
	"strings"
	"testing"
)

func testRates() Rates {
	return Rates{
		ByClass: map[string]Rate{
			"city":  {PerKm: 30, PerMin: 12},
			"cargo": {PerKm: 55, PerMin: 18},
		},
		CapCents: 5000,
	}
}

func TestParseTripFields(t *testing.T) {
	tr, err := parseTrip("T1, city, 12, 34")
	if err != nil {
		t.Fatalf("parseTrip: %v", err)
	}
	want := Trip{ID: "T1", Class: "city", Km: 12, Min: 34}
	if tr != want {
		t.Fatalf("parseTrip = %+v, want %+v", tr, want)
	}
}

func TestLoadTripsSkipsBlankLines(t *testing.T) {
	trips, err := LoadTrips("T1,city,12,34\n\n  \nT2,cargo,3,15\n")
	if err != nil {
		t.Fatalf("LoadTrips: %v", err)
	}
	if len(trips) != 2 || trips[0].ID != "T1" || trips[1].ID != "T2" {
		t.Fatalf("LoadTrips = %+v, want T1 and T2", trips)
	}
}

func TestLoadTripsAbortsOnUnparseableRow(t *testing.T) {
	trips, err := LoadTrips("T1,city,12\n")
	if err == nil {
		t.Fatal("LoadTrips accepted a 3-field row without reporting a problem")
	}
	if !strings.Contains(err.Error(), "line 1") {
		t.Fatalf("LoadTrips error %q should point at line 1", err)
	}
	if trips != nil {
		t.Fatalf("LoadTrips returned trips %+v alongside a parse failure", trips)
	}
}

func TestLoadTripsReportsInvalidRows(t *testing.T) {
	trips, err := LoadTrips("T1,city,12,34\nT9,city,-4,10\nT2,cargo,3,15\n")
	if len(trips) != 2 || trips[0].ID != "T1" || trips[1].ID != "T2" {
		t.Fatalf("LoadTrips kept %+v, want the two valid trips", trips)
	}
	if err == nil {
		t.Fatal("LoadTrips skipped an invalid row without reporting it")
	}
	if !strings.Contains(err.Error(), "line 2") || !strings.Contains(err.Error(), "negative") {
		t.Fatalf("LoadTrips error %q should point at line 2's negative distance", err)
	}
}

func TestSettleTotalsStatement(t *testing.T) {
	trips := []Trip{
		{ID: "T1", Class: "city", Km: 12, Min: 34},
		{ID: "T2", Class: "cargo", Km: 3, Min: 15},
	}
	st, err := Settle(trips, testRates())
	if err != nil {
		t.Fatalf("Settle: %v", err)
	}
	if len(st.Lines) != 2 {
		t.Fatalf("Settle produced %d lines, want 2", len(st.Lines))
	}
	if st.Lines[0].Amount != 768 || st.Lines[1].Amount != 435 {
		t.Fatalf("Settle line amounts = %d, %d; want 768, 435",
			st.Lines[0].Amount, st.Lines[1].Amount)
	}
	if st.Total != 1203 {
		t.Fatalf("Settle total = %d, want 1203", st.Total)
	}
}

func TestSettleAppliesNightlyCap(t *testing.T) {
	trips := []Trip{{ID: "T7", Class: "city", Km: 200, Min: 0}}
	st, err := Settle(trips, testRates())
	if err != nil {
		t.Fatalf("Settle: %v", err)
	}
	if st.Total != 5000 {
		t.Fatalf("Settle total = %d, want the 5000 cap", st.Total)
	}
}

func TestSettleReportsUnpriceableClass(t *testing.T) {
	trips := []Trip{
		{ID: "T1", Class: "city", Km: 12, Min: 34},
		{ID: "TX", Class: "scooter", Km: 2, Min: 9},
		{ID: "T2", Class: "cargo", Km: 3, Min: 15},
	}
	st, err := Settle(trips, testRates())
	if len(st.Lines) != 2 {
		t.Fatalf("Settle produced %d lines, want the 2 priceable trips", len(st.Lines))
	}
	if st.Total != 1203 {
		t.Fatalf("Settle total = %d, want 1203", st.Total)
	}
	if err == nil {
		t.Fatal("Settle dropped trip TX without reporting a problem")
	}
	if !strings.Contains(err.Error(), "scooter") {
		t.Fatalf("Settle error %q should name the missing class", err)
	}
}
