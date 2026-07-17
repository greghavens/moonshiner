package batch

import "testing"

func mustLines(t *testing.T, b Batch, want []Line) {
	t.Helper()
	got := b.Lines()
	if len(got) != len(want) {
		t.Fatalf("lines = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("lines = %v, want %v", got, want)
		}
	}
}

func TestSiblingBatchesAreIsolated(t *testing.T) {
	// Importer-style construction: the backing slice has room to grow.
	backing := make([]Line, 0, 8)
	backing = append(backing, Line{SKU: "CRATE-1", Qty: 1}, Line{SKU: "CRATE-2", Qty: 2})
	parent := FromLines(backing)

	d1 := parent.Append(Line{SKU: "LAMP-9", Qty: 5})
	d2 := parent.Append(Line{SKU: "DESK-4", Qty: 7})

	mustLines(t, parent, []Line{{SKU: "CRATE-1", Qty: 1}, {SKU: "CRATE-2", Qty: 2}})
	mustLines(t, d1, []Line{{SKU: "CRATE-1", Qty: 1}, {SKU: "CRATE-2", Qty: 2}, {SKU: "LAMP-9", Qty: 5}})
	mustLines(t, d2, []Line{{SKU: "CRATE-1", Qty: 1}, {SKU: "CRATE-2", Qty: 2}, {SKU: "DESK-4", Qty: 7}})
}

func TestHeadThenAppendLeavesParentIntact(t *testing.T) {
	parent := FromLines([]Line{{SKU: "A-1", Qty: 1}, {SKU: "B-2", Qty: 2}, {SKU: "C-3", Qty: 3}})
	short := parent.Head(2)
	grown := short.Append(Line{SKU: "Z-9", Qty: 9})

	mustLines(t, parent, []Line{{SKU: "A-1", Qty: 1}, {SKU: "B-2", Qty: 2}, {SKU: "C-3", Qty: 3}})
	mustLines(t, short, []Line{{SKU: "A-1", Qty: 1}, {SKU: "B-2", Qty: 2}})
	mustLines(t, grown, []Line{{SKU: "A-1", Qty: 1}, {SKU: "B-2", Qty: 2}, {SKU: "Z-9", Qty: 9}})
}

func TestFullCapacityBatchesBehaveTheSame(t *testing.T) {
	// Exact-capacity construction must give identical semantics to the
	// spare-capacity paths above.
	exact := make([]Line, 2)
	exact[0] = Line{SKU: "CRATE-1", Qty: 1}
	exact[1] = Line{SKU: "CRATE-2", Qty: 2}
	parent := FromLines(exact)

	d1 := parent.Append(Line{SKU: "LAMP-9", Qty: 5})
	d2 := parent.Append(Line{SKU: "DESK-4", Qty: 7})

	mustLines(t, parent, []Line{{SKU: "CRATE-1", Qty: 1}, {SKU: "CRATE-2", Qty: 2}})
	mustLines(t, d1, []Line{{SKU: "CRATE-1", Qty: 1}, {SKU: "CRATE-2", Qty: 2}, {SKU: "LAMP-9", Qty: 5}})
	mustLines(t, d2, []Line{{SKU: "CRATE-1", Qty: 1}, {SKU: "CRATE-2", Qty: 2}, {SKU: "DESK-4", Qty: 7}})
}

func TestCallerSliceEditsDoNotLeakIn(t *testing.T) {
	src := []Line{{SKU: "A-1", Qty: 1}, {SKU: "B-2", Qty: 2}}
	b := FromLines(src)
	src[0] = Line{SKU: "TAMPERED", Qty: 99}
	mustLines(t, b, []Line{{SKU: "A-1", Qty: 1}, {SKU: "B-2", Qty: 2}})
}

func TestLinesReturnsAnOwnedCopy(t *testing.T) {
	b := FromLines([]Line{{SKU: "A-1", Qty: 1}, {SKU: "B-2", Qty: 2}})
	got := b.Lines()
	got[0] = Line{SKU: "TAMPERED", Qty: 99}
	got = append(got, Line{SKU: "EXTRA", Qty: 1})
	_ = got
	mustLines(t, b, []Line{{SKU: "A-1", Qty: 1}, {SKU: "B-2", Qty: 2}})
	if b.Len() != 2 {
		t.Fatalf("Len = %d, want 2", b.Len())
	}
}

func TestAppendOrderAndEmptySemantics(t *testing.T) {
	var zero Batch
	if zero.Len() != 0 || len(zero.Lines()) != 0 {
		t.Fatalf("zero batch not empty: %v", zero.Lines())
	}
	fromNil := FromLines(nil)
	if fromNil.Len() != 0 || len(fromNil.Lines()) != 0 {
		t.Fatalf("FromLines(nil) not empty: %v", fromNil.Lines())
	}
	b := zero.Append(Line{SKU: "A-1", Qty: 1}).Append(Line{SKU: "B-2", Qty: 2}).Append(Line{SKU: "C-3", Qty: 3})
	mustLines(t, b, []Line{{SKU: "A-1", Qty: 1}, {SKU: "B-2", Qty: 2}, {SKU: "C-3", Qty: 3}})
	if zero.Len() != 0 {
		t.Fatalf("appending grew the zero batch: %v", zero.Lines())
	}
	if head := b.Head(0); head.Len() != 0 {
		t.Fatalf("Head(0) not empty: %v", head.Lines())
	}
	if head := b.Head(10); head.Len() != 3 {
		t.Fatalf("Head beyond length = %v", head.Lines())
	}
}
