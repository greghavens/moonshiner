package idxplan

// Planner contract:
//   - Eq queries prefer a hash index on the field; with no hash index they
//     use an ordered index; with neither they scan the heap.
//   - Range queries (lo <= value < hi, byte order, nil = unbounded) can only
//     be served by an ordered index; hash indexes never serve ranges.
//   - When several indexes of the winning kind cover the field, the planner
//     picks the lexicographically smallest index name.
//   - Explain returns the exact Plan struct Search would execute; Search
//     always returns matching record ids in ascending id order.
//   - ScanCount() counts Searches that fell back to a full heap scan.

import (
	"reflect"
	"slices"
	"strings"
	"testing"
)

func sp(s string) *string { return &s }

// seedHeap: six shipment records, hash index on wh, ordered index on sku.
//
//	id 1: sku P-1001  wh OSL  qty 12
//	id 2: sku P-1004  wh BER  qty 3
//	id 3: sku P-1002  wh OSL  qty 7
//	id 4: sku P-1006  wh AMS  qty 12
//	id 5: sku P-1003  wh OSL  qty 1
//	id 6: sku P-1005  wh BER  (no qty)
func seedHeap(t *testing.T) *Heap {
	t.Helper()
	h := NewHeap()
	for _, rec := range []map[string]string{
		{"sku": "P-1001", "wh": "OSL", "qty": "12"},
		{"sku": "P-1004", "wh": "BER", "qty": "3"},
		{"sku": "P-1002", "wh": "OSL", "qty": "7"},
		{"sku": "P-1006", "wh": "AMS", "qty": "12"},
		{"sku": "P-1003", "wh": "OSL", "qty": "1"},
		{"sku": "P-1005", "wh": "BER"},
	} {
		h.Insert(rec)
	}
	if err := h.CreateHashIndex("by_wh", "wh"); err != nil {
		t.Fatalf("CreateHashIndex: %v", err)
	}
	if err := h.CreateOrderedIndex("by_sku", "sku"); err != nil {
		t.Fatalf("CreateOrderedIndex: %v", err)
	}
	return h
}

func wantPlan(t *testing.T, h *Heap, q Query, want Plan) {
	t.Helper()
	got, err := h.Explain(q)
	if err != nil {
		t.Fatalf("Explain: %v", err)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Explain = %+v, want %+v", got, want)
	}
}

func wantIDs(t *testing.T, h *Heap, q Query, want ...uint64) {
	t.Helper()
	got, err := h.Search(q)
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if !slices.Equal(got, want) {
		t.Fatalf("Search = %v, want %v", got, want)
	}
}

func TestExplainPinsThePlanExactly(t *testing.T) {
	h := seedHeap(t)
	wantPlan(t, h, Eq("wh", "OSL"),
		Plan{Kind: "hash", Index: "by_wh", Field: "wh", Eq: sp("OSL")})
	wantPlan(t, h, Eq("sku", "P-1003"),
		Plan{Kind: "ordered", Index: "by_sku", Field: "sku", Eq: sp("P-1003")})
	wantPlan(t, h, Range("sku", sp("P-1002"), sp("P-1005")),
		Plan{Kind: "ordered", Index: "by_sku", Field: "sku", Lo: sp("P-1002"), Hi: sp("P-1005")})
	wantPlan(t, h, Range("sku", nil, nil),
		Plan{Kind: "ordered", Index: "by_sku", Field: "sku"})
	wantPlan(t, h, Eq("qty", "12"),
		Plan{Kind: "scan", Field: "qty", Eq: sp("12")})
	wantPlan(t, h, Range("wh", sp("B"), sp("C")),
		Plan{Kind: "scan", Field: "wh", Lo: sp("B"), Hi: sp("C")})
	wantPlan(t, h, Range("qty", sp("1"), nil),
		Plan{Kind: "scan", Field: "qty", Lo: sp("1")})
}

func TestSearchByHashAndOrderedIndexes(t *testing.T) {
	h := seedHeap(t)
	wantIDs(t, h, Eq("wh", "OSL"), 1, 3, 5)
	wantIDs(t, h, Eq("wh", "AMS"), 4)
	wantIDs(t, h, Eq("wh", "TRD")) // no hits
	wantIDs(t, h, Eq("sku", "P-1003"), 5)
	wantIDs(t, h, Range("sku", sp("P-1002"), sp("P-1005")), 2, 3, 5)
	wantIDs(t, h, Range("sku", nil, sp("P-1003")), 1, 3)
	wantIDs(t, h, Range("sku", sp("P-1005"), nil), 4, 6)
	wantIDs(t, h, Range("sku", nil, nil), 1, 2, 3, 4, 5, 6)
	wantIDs(t, h, Range("sku", sp("P-1003"), sp("P-1003"))) // empty interval
	wantIDs(t, h, Range("sku", sp("P-1005"), sp("P-1002"))) // inverted interval
}

func TestSearchByHeapScan(t *testing.T) {
	h := seedHeap(t)
	wantIDs(t, h, Eq("qty", "12"), 1, 4)
	wantIDs(t, h, Range("qty", sp("1"), sp("2")), 1, 4, 5) // byte order: "1" <= "1","12" < "2"
	wantIDs(t, h, Range("wh", sp("B"), sp("C")), 2, 6)
}

func TestRecordsWithoutTheFieldNeverMatch(t *testing.T) {
	h := seedHeap(t)
	blank := h.Insert(map[string]string{"sku": "P-2000", "wh": ""}) // id 7: wh present but empty
	h.Insert(map[string]string{"sku": "P-2001"})                    // id 8: no wh at all
	wantIDs(t, h, Eq("wh", ""), blank)
	wantIDs(t, h, Range("wh", nil, nil), 1, 2, 3, 4, 5, 6, 7)
	wantIDs(t, h, Eq("qty", "")) // id 6 and 8 lack qty; nothing stores ""
}

func TestPlannerPrefersHashForEqAndOrderedForRange(t *testing.T) {
	h := seedHeap(t)
	if err := h.CreateOrderedIndex("wh_sorted", "wh"); err != nil {
		t.Fatalf("CreateOrderedIndex: %v", err)
	}
	wantPlan(t, h, Eq("wh", "BER"),
		Plan{Kind: "hash", Index: "by_wh", Field: "wh", Eq: sp("BER")})
	wantPlan(t, h, Range("wh", sp("B"), sp("C")),
		Plan{Kind: "ordered", Index: "wh_sorted", Field: "wh", Lo: sp("B"), Hi: sp("C")})
	wantIDs(t, h, Range("wh", sp("B"), sp("C")), 2, 6)
}

func TestPlannerTieBreaksByIndexName(t *testing.T) {
	h := seedHeap(t)
	if err := h.CreateHashIndex("aa_wh", "wh"); err != nil {
		t.Fatalf("CreateHashIndex: %v", err)
	}
	wantPlan(t, h, Eq("wh", "OSL"),
		Plan{Kind: "hash", Index: "aa_wh", Field: "wh", Eq: sp("OSL")})
}

func TestCreateIndexBackfillsExistingRecords(t *testing.T) {
	h := NewHeap()
	h.Insert(map[string]string{"sku": "B"})
	h.Insert(map[string]string{"sku": "A"})
	h.Insert(map[string]string{"sku": "C"})
	if err := h.CreateOrderedIndex("by_sku", "sku"); err != nil {
		t.Fatalf("CreateOrderedIndex: %v", err)
	}
	wantPlan(t, h, Range("sku", sp("A"), sp("C")),
		Plan{Kind: "ordered", Index: "by_sku", Field: "sku", Lo: sp("A"), Hi: sp("C")})
	wantIDs(t, h, Range("sku", sp("A"), sp("C")), 1, 2)
}

func TestOrderedIndexHandlesDuplicateValues(t *testing.T) {
	h := NewHeap()
	h.Insert(map[string]string{"sku": "P-3000"})
	h.Insert(map[string]string{"sku": "P-2000"})
	h.Insert(map[string]string{"sku": "P-3000"})
	if err := h.CreateOrderedIndex("by_sku", "sku"); err != nil {
		t.Fatalf("CreateOrderedIndex: %v", err)
	}
	wantIDs(t, h, Eq("sku", "P-3000"), 1, 3)
	wantIDs(t, h, Range("sku", sp("P-2500"), nil), 1, 3)
}

func TestUpdateMaintainsEveryIndex(t *testing.T) {
	h := seedHeap(t)
	// move id 3 from OSL to AMS and to a far-away sku
	if err := h.Update(3, map[string]string{"sku": "P-9000", "wh": "AMS", "qty": "7"}); err != nil {
		t.Fatalf("Update: %v", err)
	}
	wantIDs(t, h, Eq("wh", "OSL"), 1, 5)
	wantIDs(t, h, Eq("wh", "AMS"), 3, 4)
	wantIDs(t, h, Range("sku", sp("P-1002"), sp("P-1005")), 2, 5)
	wantIDs(t, h, Eq("sku", "P-9000"), 3)

	// dropping the indexed field entirely removes the entry
	if err := h.Update(3, map[string]string{"qty": "7"}); err != nil {
		t.Fatalf("Update: %v", err)
	}
	wantIDs(t, h, Eq("wh", "AMS"), 4)
	wantIDs(t, h, Range("sku", nil, nil), 1, 2, 4, 5, 6)
}

func TestDeleteMaintainsEveryIndex(t *testing.T) {
	h := seedHeap(t)
	if err := h.Delete(5); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	wantIDs(t, h, Eq("wh", "OSL"), 1, 3)
	wantIDs(t, h, Range("sku", sp("P-1002"), sp("P-1005")), 2, 3)
	wantIDs(t, h, Eq("sku", "P-1003"))
}

func TestScanCountTracksOnlyHeapScans(t *testing.T) {
	h := seedHeap(t)
	if n := h.ScanCount(); n != 0 {
		t.Fatalf("fresh ScanCount = %d, want 0", n)
	}
	if _, err := h.Explain(Eq("qty", "12")); err != nil {
		t.Fatalf("Explain: %v", err)
	}
	if n := h.ScanCount(); n != 0 {
		t.Fatalf("ScanCount after Explain = %d, want 0 (Explain must not execute)", n)
	}
	wantIDs(t, h, Eq("wh", "OSL"), 1, 3, 5)
	wantIDs(t, h, Range("sku", nil, sp("P-1003")), 1, 3)
	if n := h.ScanCount(); n != 0 {
		t.Fatalf("ScanCount after indexed searches = %d, want 0", n)
	}
	wantIDs(t, h, Eq("qty", "12"), 1, 4)
	wantIDs(t, h, Range("wh", sp("B"), sp("C")), 2, 6)
	if n := h.ScanCount(); n != 2 {
		t.Fatalf("ScanCount after two scan searches = %d, want 2", n)
	}
}

func TestIndexAndQueryValidation(t *testing.T) {
	h := seedHeap(t)
	err := h.CreateHashIndex("by_wh", "qty")
	if err == nil || !strings.Contains(err.Error(), "by_wh") {
		t.Fatalf("duplicate index name: err = %v, want an error naming by_wh", err)
	}
	if err := h.CreateOrderedIndex("by_wh", "qty"); err == nil {
		t.Fatal("duplicate index name across kinds must be rejected")
	}
	if _, err := h.Explain(Query{}); err == nil {
		t.Fatal("Explain of a query with no field must error")
	}
	if _, err := h.Search(Query{}); err == nil {
		t.Fatal("Search of a query with no field must error")
	}
}
