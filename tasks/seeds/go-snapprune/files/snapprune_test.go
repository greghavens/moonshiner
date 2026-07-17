package snapprune

import (
	"reflect"
	"testing"
	"time"
)

// Pins the existing name-parsing and keep-last-N behavior.

func TestTimestampParsesUTC(t *testing.T) {
	ts, err := Timestamp("db-primary-20260711-031500")
	if err != nil {
		t.Fatal(err)
	}
	want := time.Date(2026, 7, 11, 3, 15, 0, 0, time.UTC)
	if !ts.Equal(want) || ts.Location() != time.UTC {
		t.Fatalf("Timestamp = %v (%v), want %v UTC", ts, ts.Location(), want)
	}
}

func TestTimestampRejectsMalformedNames(t *testing.T) {
	bad := []string{
		"",
		"db",
		"db-20260711",           // date only
		"db-2026071-031500",     // short date
		"db-20260711-0315000",   // long time
		"db_20260711-031500",    // missing dash before stamp
		"db-20261341-031500",    // month 13
		"db-20260711-256000",    // hour 25
		"20260711-031500",       // no prefix at all
	}
	for _, name := range bad {
		if _, err := Timestamp(name); err == nil {
			t.Fatalf("Timestamp(%q) succeeded, want error", name)
		}
	}
}

func TestKeepLastNSplitsNewestFirst(t *testing.T) {
	names := []string{
		"db-20260401-120000",
		"db-20260403-120000",
		"db-20260402-120000",
		"db-20260404-120000",
	}
	keep, drop, err := KeepLastN(names, 2)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(keep, []string{"db-20260404-120000", "db-20260403-120000"}) {
		t.Fatalf("keep = %v", keep)
	}
	if !reflect.DeepEqual(drop, []string{"db-20260402-120000", "db-20260401-120000"}) {
		t.Fatalf("drop = %v", drop)
	}
}

func TestKeepLastNTiesBreakByNameAscending(t *testing.T) {
	names := []string{"beta-20260401-120000", "alpha-20260401-120000"}
	keep, drop, err := KeepLastN(names, 1)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(keep, []string{"alpha-20260401-120000"}) {
		t.Fatalf("keep = %v", keep)
	}
	if !reflect.DeepEqual(drop, []string{"beta-20260401-120000"}) {
		t.Fatalf("drop = %v", drop)
	}
}

func TestKeepLastNKeepsEverythingWhenNIsLarge(t *testing.T) {
	names := []string{"db-20260401-120000", "db-20260402-120000"}
	keep, drop, err := KeepLastN(names, 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(keep) != 2 || len(drop) != 0 {
		t.Fatalf("keep = %v, drop = %v", keep, drop)
	}
}

func TestKeepLastNRejectsNonPositiveN(t *testing.T) {
	for _, n := range []int{0, -1} {
		if _, _, err := KeepLastN([]string{"db-20260401-120000"}, n); err == nil {
			t.Fatalf("KeepLastN(_, %d) succeeded, want error", n)
		}
	}
}

func TestKeepLastNPropagatesParseErrors(t *testing.T) {
	if _, _, err := KeepLastN([]string{"db-20260401-120000", "manual-copy"}, 1); err == nil {
		t.Fatal("KeepLastN with a malformed name succeeded, want error")
	}
}
