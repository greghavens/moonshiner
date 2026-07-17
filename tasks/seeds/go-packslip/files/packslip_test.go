package packslip

import (
	"os"
	"path/filepath"
	"testing"
)

var bigOrder = Order{
	ID:   "ORD-3117",
	Tags: []string{"fragile", "upstairs"},
	Items: []Item{
		{SKU: "CH-220", Name: "Anodized Rail Kit", Qty: 2},
		{SKU: "PL-050", Name: `Cedar Planter 12"`, Qty: 1},
	},
	Note: "fragile\nkeep flat\tno stacking",
}

func TestRenderMatchesLabelStationFormat(t *testing.T) {
	want := "PACKSLIP ORD-3117\n" +
		"TAGS fragile,upstairs\n" +
		"ITEM CH-220 qty=2 Anodized Rail Kit\n" +
		"ITEM PL-050 qty=1 Cedar Planter 12\"\n" +
		"NOTE \"fragile\\nkeep flat\\tno stacking\"\n" +
		"END 2\n"
	if got := Render(bigOrder); got != want {
		t.Fatalf("slip mismatch:\n got:\n%s\nwant:\n%s", got, want)
	}
}

func TestRenderWithoutTagsAndEmptyNote(t *testing.T) {
	o := Order{
		ID:    "ORD-8",
		Items: []Item{{SKU: "BX-1", Name: "Box", Qty: 3}},
	}
	want := "PACKSLIP ORD-8\n" +
		"ITEM BX-1 qty=3 Box\n" +
		"NOTE \"\"\n" +
		"END 1\n"
	if got := Render(o); got != want {
		t.Fatalf("slip mismatch:\n got:\n%s\nwant:\n%s", got, want)
	}
}

func TestSlipLineCountIsStable(t *testing.T) {
	got := Render(bigOrder)
	lines := 0
	for _, r := range got {
		if r == '\n' {
			lines++
		}
	}
	// header + tags + 2 items + note + end: the label printer pre-cuts
	// the roll based on this count, so a note may never add lines.
	if lines != 6 {
		t.Fatalf("slip has %d lines, want 6:\n%s", lines, got)
	}
}

func TestWritePutsSlipInsideOutputDir(t *testing.T) {
	dir := t.TempDir()
	gotPath, err := Write(bigOrder, dir)
	if err != nil {
		t.Fatalf("write: %v", err)
	}
	wantPath := filepath.Join(dir, "ORD-3117.slip")
	if gotPath != wantPath {
		t.Fatalf("returned path = %q, want %q", gotPath, wantPath)
	}
	data, err := os.ReadFile(wantPath)
	if err != nil {
		t.Fatalf("slip not readable where the label station looks: %v", err)
	}
	if string(data) != Render(bigOrder) {
		t.Fatalf("file content differs from rendered slip")
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("read dir: %v", err)
	}
	if len(entries) != 1 || entries[0].Name() != "ORD-3117.slip" {
		names := make([]string, 0, len(entries))
		for _, e := range entries {
			names = append(names, e.Name())
		}
		t.Fatalf("output dir contains %v, want exactly [ORD-3117.slip]", names)
	}
}
