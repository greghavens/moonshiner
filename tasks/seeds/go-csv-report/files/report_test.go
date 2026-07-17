package report

import (
	"strings"
	"testing"
)

func mustReport(t *testing.T, cols ...string) *Report {
	t.Helper()
	r, err := New(cols...)
	if err != nil {
		t.Fatalf("New(%v): %v", cols, err)
	}
	return r
}

func TestCSVHeaderAndColumnOrder(t *testing.T) {
	r := mustReport(t, "id", "name", "total")
	if err := r.Add(map[string]string{"name": "ana", "id": "7", "total": "12.50"}); err != nil {
		t.Fatalf("Add: %v", err)
	}
	got, err := r.CSV()
	if err != nil {
		t.Fatalf("CSV: %v", err)
	}
	want := "id,name,total\n7,ana,12.50\n"
	if got != want {
		t.Fatalf("CSV() = %q, want %q", got, want)
	}
}

func TestCSVMissingKeysBecomeEmptyCells(t *testing.T) {
	r := mustReport(t, "id", "name", "email")
	if err := r.Add(map[string]string{"id": "1"}); err != nil {
		t.Fatalf("Add: %v", err)
	}
	got, err := r.CSV()
	if err != nil {
		t.Fatalf("CSV: %v", err)
	}
	want := "id,name,email\n1,,\n"
	if got != want {
		t.Fatalf("CSV() = %q, want %q", got, want)
	}
}

func TestCSVQuotesSpecialCharacters(t *testing.T) {
	r := mustReport(t, "id", "note")
	if err := r.Add(map[string]string{"id": "1", "note": `say "hi", ok`}); err != nil {
		t.Fatalf("Add: %v", err)
	}
	got, err := r.CSV()
	if err != nil {
		t.Fatalf("CSV: %v", err)
	}
	want := "id,note\n1,\"say \"\"hi\"\", ok\"\n"
	if got != want {
		t.Fatalf("CSV() = %q, want %q", got, want)
	}
}

func TestAddRejectsUnknownColumn(t *testing.T) {
	r := mustReport(t, "id")
	if err := r.Add(map[string]string{"id": "1", "surprise": "x"}); err == nil {
		t.Fatal("Add accepted a key that is not a declared column")
	}
	if r.RowCount() != 0 {
		t.Fatalf("RowCount() = %d after a rejected Add, want 0", r.RowCount())
	}
}

func TestNewValidatesColumns(t *testing.T) {
	if _, err := New(); err == nil {
		t.Fatal("New() with no columns should fail")
	}
	if _, err := New("id", ""); err == nil {
		t.Fatal("New accepted an empty column name")
	}
	if _, err := New("id", "id"); err == nil {
		t.Fatal("New accepted duplicate columns")
	}
}

func TestEmptyReportRendersHeaderOnly(t *testing.T) {
	r := mustReport(t, "a", "b")
	got, err := r.CSV()
	if err != nil {
		t.Fatalf("CSV: %v", err)
	}
	if got != "a,b\n" {
		t.Fatalf("CSV() = %q, want header only", got)
	}
	if !strings.HasSuffix(got, "\n") {
		t.Fatal("CSV output must end with a newline")
	}
}

func TestColumnsReturnsACopy(t *testing.T) {
	r := mustReport(t, "a", "b")
	cols := r.Columns()
	cols[0] = "mutated"
	if r.Columns()[0] != "a" {
		t.Fatal("mutating the Columns() result leaked into the report")
	}
}
