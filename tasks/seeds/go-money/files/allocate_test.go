package money

import (
	"reflect"
	"testing"
)

// Acceptance tests for proportional allocation: Money.Allocate with
// the largest-remainder method (exact sum preservation, deterministic
// tie-break toward earlier ratios) and the Money.Split convenience.

func mustAllocate(t *testing.T, m Money, ratios []int) []Money {
	t.Helper()
	parts, err := m.Allocate(ratios)
	if err != nil {
		t.Fatalf("Allocate(%v, %v): %v", m, ratios, err)
	}
	return parts
}

func TestAllocateExactProportionsNeedNoRounding(t *testing.T) {
	parts := mustAllocate(t, New(10000, "USD"), []int{1, 3})
	want := []Money{New(2500, "USD"), New(7500, "USD")}
	if !reflect.DeepEqual(parts, want) {
		t.Fatalf("Allocate = %v, want %v", parts, want)
	}
}

func TestAllocateLeftoverCentGoesToEarliestOnTies(t *testing.T) {
	parts := mustAllocate(t, New(100, "USD"), []int{1, 1, 1})
	want := []Money{New(34, "USD"), New(33, "USD"), New(33, "USD")}
	if !reflect.DeepEqual(parts, want) {
		t.Fatalf("Allocate(1.00, [1 1 1]) = %v, want %v", parts, want)
	}

	parts = mustAllocate(t, New(101, "USD"), []int{1, 1})
	want = []Money{New(51, "USD"), New(50, "USD")}
	if !reflect.DeepEqual(parts, want) {
		t.Fatalf("Allocate(1.01, [1 1]) = %v, want %v", parts, want)
	}
}

func TestAllocateGivesLeftoversToLargestRemaindersNotFirstComers(t *testing.T) {
	// 1.00 over [3,1,3]: raw shares 42.857 / 14.285 / 42.857 — the two
	// outer parts have the biggest fractional loss, so the two leftover
	// cents belong to them, NOT to the first two slots.
	parts := mustAllocate(t, New(100, "USD"), []int{3, 1, 3})
	want := []Money{New(43, "USD"), New(14, "USD"), New(43, "USD")}
	if !reflect.DeepEqual(parts, want) {
		t.Fatalf("Allocate(1.00, [3 1 3]) = %v, want %v", parts, want)
	}
}

func TestAllocateZeroRatioReceivesNothing(t *testing.T) {
	parts := mustAllocate(t, New(100, "USD"), []int{0, 1})
	want := []Money{New(0, "USD"), New(100, "USD")}
	if !reflect.DeepEqual(parts, want) {
		t.Fatalf("Allocate(1.00, [0 1]) = %v, want %v", parts, want)
	}
	parts = mustAllocate(t, New(101, "USD"), []int{1, 0, 1})
	want = []Money{New(51, "USD"), New(0, "USD"), New(50, "USD")}
	if !reflect.DeepEqual(parts, want) {
		t.Fatalf("Allocate(1.01, [1 0 1]) = %v, want %v", parts, want)
	}
}

func TestAllocateNegativeAmountMirrorsPositive(t *testing.T) {
	parts := mustAllocate(t, New(-101, "USD"), []int{1, 1})
	want := []Money{New(-51, "USD"), New(-50, "USD")}
	if !reflect.DeepEqual(parts, want) {
		t.Fatalf("Allocate(-1.01, [1 1]) = %v, want %v", parts, want)
	}
}

func TestAllocateZeroAmount(t *testing.T) {
	parts := mustAllocate(t, New(0, "USD"), []int{2, 5, 3})
	want := []Money{New(0, "USD"), New(0, "USD"), New(0, "USD")}
	if !reflect.DeepEqual(parts, want) {
		t.Fatalf("Allocate(0, [2 5 3]) = %v, want %v", parts, want)
	}
}

func TestAllocateNeverCreatesOrLosesACent(t *testing.T) {
	ratioSets := [][]int{{1, 1}, {1, 2, 3}, {7, 3}, {5, 5, 5, 5}, {9, 1, 90}}
	for amount := int64(0); amount <= 300; amount++ {
		for _, ratios := range ratioSets {
			parts := mustAllocate(t, New(amount, "EUR"), ratios)
			if len(parts) != len(ratios) {
				t.Fatalf("amount %d ratios %v: %d parts, want %d", amount, ratios, len(parts), len(ratios))
			}
			var sum int64
			for _, p := range parts {
				if p.Currency() != "EUR" {
					t.Fatalf("amount %d ratios %v: part currency %q, want EUR", amount, ratios, p.Currency())
				}
				sum += p.Amount()
			}
			if sum != amount {
				t.Fatalf("amount %d ratios %v: parts %v sum to %d", amount, ratios, parts, sum)
			}
		}
	}
}

func TestAllocateIsDeterministic(t *testing.T) {
	m := New(9999, "USD")
	ratios := []int{3, 3, 3, 1}
	first := mustAllocate(t, m, ratios)
	for i := 0; i < 10; i++ {
		if got := mustAllocate(t, m, ratios); !reflect.DeepEqual(got, first) {
			t.Fatalf("run %d: Allocate = %v, first run = %v", i+2, got, first)
		}
	}
}

func TestAllocateRejectsBadRatios(t *testing.T) {
	m := New(100, "USD")
	if _, err := m.Allocate(nil); err == nil {
		t.Fatal("Allocate(nil) must error")
	}
	if _, err := m.Allocate([]int{}); err == nil {
		t.Fatal("Allocate(empty) must error")
	}
	if _, err := m.Allocate([]int{2, -1}); err == nil {
		t.Fatal("Allocate with a negative ratio must error")
	}
	if _, err := m.Allocate([]int{0, 0, 0}); err == nil {
		t.Fatal("Allocate with all-zero ratios must error")
	}
}

func TestSplitDividesEvenlyWithLeftoverCentsUpFront(t *testing.T) {
	parts, err := New(100, "USD").Split(3)
	if err != nil {
		t.Fatalf("Split: %v", err)
	}
	want := []Money{New(34, "USD"), New(33, "USD"), New(33, "USD")}
	if !reflect.DeepEqual(parts, want) {
		t.Fatalf("Split(1.00, 3) = %v, want %v", parts, want)
	}

	parts, err = New(5, "USD").Split(4)
	if err != nil {
		t.Fatalf("Split: %v", err)
	}
	want = []Money{New(2, "USD"), New(1, "USD"), New(1, "USD"), New(1, "USD")}
	if !reflect.DeepEqual(parts, want) {
		t.Fatalf("Split(0.05, 4) = %v, want %v", parts, want)
	}
}

func TestSplitRejectsNonPositiveN(t *testing.T) {
	if _, err := New(100, "USD").Split(0); err == nil {
		t.Fatal("Split(0) must error")
	}
	if _, err := New(100, "USD").Split(-2); err == nil {
		t.Fatal("Split(-2) must error")
	}
}
