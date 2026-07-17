package units

// Acceptance for the instance-based registry with functional options.
// Every conversion value and error string pinned here was captured from
// the package-global implementation — behavior must carry over through
// New(WithStandardUnits()).

import (
	"strings"
	"testing"
)

func mustNew(t *testing.T, opts ...Option) *Registry {
	t.Helper()
	r, err := New(opts...)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return r
}

func convert(t *testing.T, r *Registry, v float64, from, to string) float64 {
	t.Helper()
	got, err := r.Convert(v, from, to)
	if err != nil {
		t.Fatalf("Convert(%v, %s, %s): %v", v, from, to, err)
	}
	return got
}

func TestStandardRegistryMatchesTheOldGlobalTable(t *testing.T) {
	r := mustNew(t, WithStandardUnits())
	cases := []struct {
		v        float64
		from, to string
		want     float64
	}{
		{1500, "m", "km", 1.5},
		{3, "mi", "km", 4.828},
		{2.5, "h", "min", 150},
		{1, "lb", "g", 453.5924}, // default precision is 4, same as before
		{2, "oz", "g", 56.699},
		{90, "min", "h", 1.5},
		{2, "kilometer", "m", 2000}, // standard aliases come along
	}
	for _, c := range cases {
		if got := convert(t, r, c.v, c.from, c.to); got != c.want {
			t.Errorf("Convert(%v, %s, %s) = %v, want %v", c.v, c.from, c.to, got, c.want)
		}
	}
}

func TestFreshRegistryStartsEmpty(t *testing.T) {
	r := mustNew(t)
	_, err := r.Convert(1, "m", "km")
	if err == nil || !strings.Contains(err.Error(), "unknown unit: m") {
		t.Fatalf("empty registry Convert err = %v, want unknown unit: m", err)
	}
}

func TestOptionsCompose(t *testing.T) {
	r := mustNew(t,
		WithStandardUnits(),
		WithPrecision(2),
		WithUnit("nmi", Length, 1852),
		WithAlias("nautical", "nmi"),
	)
	if got := convert(t, r, 2, "nautical", "km"); got != 3.7 {
		t.Fatalf("2 nautical -> km = %v, want 3.7", got)
	}
	// the precision option applies to the standard units too
	if got := convert(t, r, 3, "mi", "km"); got != 4.83 {
		t.Fatalf("3 mi -> km at precision 2 = %v, want 4.83", got)
	}
}

func TestTheLastPrecisionOptionWins(t *testing.T) {
	r := mustNew(t, WithStandardUnits(), WithPrecision(5), WithPrecision(1))
	if got := convert(t, r, 3, "mi", "km"); got != 4.8 {
		t.Fatalf("3 mi -> km at precision 1 = %v, want 4.8", got)
	}
}

func TestBrokenOptionsFailConstruction(t *testing.T) {
	cases := []struct {
		name string
		opts []Option
		want string
	}{
		{"alias to a unit that is not there", []Option{WithAlias("nautical", "nmi")}, "unknown unit: nmi"},
		{"duplicate of a standard unit", []Option{WithStandardUnits(), WithUnit("m", Length, 2)}, "unit already registered: m"},
		{"duplicate of a standard alias", []Option{WithStandardUnits(), WithAlias("meter", "m")}, "unit already registered: meter"},
		{"precision out of range", []Option{WithPrecision(12)}, "precision out of range: 12"},
		{"non-positive factor", []Option{WithUnit("bogus", Length, 0)}, "factor for bogus must be positive"},
	}
	for _, c := range cases {
		_, err := New(c.opts...)
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: New err = %v, want it to mention %q", c.name, err, c.want)
		}
	}
}

func TestInstancesDoNotShareState(t *testing.T) {
	a := mustNew(t, WithStandardUnits())
	b := mustNew(t, WithStandardUnits())

	if err := a.Register("furlong", Length, 201.168); err != nil {
		t.Fatal(err)
	}
	if got := convert(t, a, 1, "furlong", "m"); got != 201.168 {
		t.Fatalf("furlong on a = %v", got)
	}
	if _, err := b.Convert(1, "furlong", "m"); err == nil ||
		!strings.Contains(err.Error(), "unknown unit: furlong") {
		t.Fatalf("b saw a's unit: err = %v", err)
	}
	// b can add the same name independently — no shared dup tracking
	if err := b.Register("furlong", Length, 201.168); err != nil {
		t.Fatalf("b.Register(furlong) = %v, want nil", err)
	}
	// and a's dup detection is its own
	if err := a.Register("furlong", Length, 201.168); err == nil ||
		!strings.Contains(err.Error(), "unit already registered: furlong") {
		t.Fatalf("a.Register duplicate err = %v", err)
	}

	if err := a.SetPrecision(1); err != nil {
		t.Fatal(err)
	}
	if got := convert(t, a, 3, "mi", "km"); got != 4.8 {
		t.Fatalf("a at precision 1: 3 mi -> km = %v, want 4.8", got)
	}
	if got := convert(t, b, 3, "mi", "km"); got != 4.828 {
		t.Fatalf("b's precision must be untouched: 3 mi -> km = %v, want 4.828", got)
	}
}

func TestRuntimeRegistrationStillWorks(t *testing.T) {
	r := mustNew(t, WithStandardUnits())
	if err := r.RegisterAlias("klick", "km"); err != nil {
		t.Fatal(err)
	}
	if got := convert(t, r, 5, "klick", "m"); got != 5000 {
		t.Fatalf("5 klick -> m = %v, want 5000", got)
	}
	if err := r.RegisterAlias("blob", "xyz"); err == nil ||
		!strings.Contains(err.Error(), "unknown unit: xyz") {
		t.Fatalf("alias to unknown err = %v", err)
	}
	if err := r.Register("", Length, 1); err == nil ||
		!strings.Contains(err.Error(), "unit name must not be empty") {
		t.Fatalf("empty name err = %v", err)
	}
	if err := r.SetPrecision(-1); err == nil ||
		!strings.Contains(err.Error(), "precision out of range: -1") {
		t.Fatalf("bad precision err = %v", err)
	}
}

func TestDimensionMismatchMessageIsUnchanged(t *testing.T) {
	r := mustNew(t, WithStandardUnits())
	_, err := r.Convert(1, "kg", "m")
	if err == nil || err.Error() != "dimension mismatch: kg is mass, m is length" {
		t.Fatalf("err = %v, want the exact legacy message", err)
	}
	_, err = r.Convert(1, "parsec", "m")
	if err == nil || err.Error() != "unknown unit: parsec" {
		t.Fatalf("err = %v, want unknown unit: parsec", err)
	}
}
