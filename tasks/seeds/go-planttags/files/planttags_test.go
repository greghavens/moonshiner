package planttags

import (
	"reflect"
	"strings"
	"testing"
)

func lavender() Tag {
	return Tag{Common: "Lavender", Latin: "Lavandula angustifolia", SunCode: "FS", Price: 8.25}
}

func TestRegistryCoversAllDevices(t *testing.T) {
	ws := Writers()
	for _, kind := range []string{"text", "csv", "badge"} {
		if _, ok := ws[kind]; !ok {
			t.Errorf("registry is missing the %q writer", kind)
		}
	}
}

func TestTextWriterRendersBenchTag(t *testing.T) {
	var w TagWriter = TextWriter{}
	got, err := w.WriteTag(lavender())
	if err != nil {
		t.Fatal(err)
	}
	want := "Lavender (Lavandula angustifolia) [FS] $8.25"
	if got != want {
		t.Errorf("text tag = %q, want %q", got, want)
	}
	if _, err := w.WriteTag(Tag{Latin: "x"}); err == nil {
		t.Error("expected an error for a tag with no common name")
	}
}

func TestCSVWriterRendersRow(t *testing.T) {
	var w TagWriter = CSVWriter{Sep: ";"}
	got, err := w.WriteTag(lavender())
	if err != nil {
		t.Fatal(err)
	}
	if got != "Lavender;Lavandula angustifolia;FS;8.25" {
		t.Errorf("csv row = %q", got)
	}
	if _, err := w.WriteTag(Tag{Common: "a;b", Latin: "c"}); err == nil {
		t.Error("expected an error when a field contains the separator")
	}
}

func TestBadgeWriterCountsPrintedTags(t *testing.T) {
	ws := Writers()
	b, ok := ws["badge"].(*BadgeWriter)
	if !ok {
		t.Fatalf("badge writer must be the shared counting instance, got %T", ws["badge"])
	}
	if _, err := b.WriteTag(lavender()); err != nil {
		t.Fatal(err)
	}
	if _, err := b.WriteTag(Tag{Common: "Thyme", SunCode: "PS", Price: 4}); err != nil {
		t.Fatal(err)
	}
	if b.Printed != 2 {
		t.Errorf("Printed = %d after two tags, want 2", b.Printed)
	}
	if _, err := b.WriteTag(Tag{Common: "no sun code"}); err == nil {
		t.Error("expected an error for a tag with no sun code")
	}
	if b.Printed != 2 {
		t.Errorf("rejected tag must not count; Printed = %d, want 2", b.Printed)
	}
}

func TestPrintRun(t *testing.T) {
	got, err := PrintRun("badge", []Tag{lavender(), {Common: "Thyme", SunCode: "PS", Price: 4}})
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"LAVENDER|FS|8.25", "THYME|PS|4.00"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("PrintRun(badge) = %v, want %v", got, want)
	}
	if _, err := PrintRun("laser", nil); err == nil {
		t.Error("expected an error for an unregistered device")
	}
	if _, err := PrintRun("text", []Tag{{Latin: "x"}}); err == nil || !strings.Contains(err.Error(), "tag") {
		t.Errorf("expected a per-tag error, got %v", err)
	}
}

func TestSampleSheet(t *testing.T) {
	got, err := SampleSheet()
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		"Rosemary (Salvia rosmarinus) [FS] $6.50",
		"ROSEMARY|FS|6.50",
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("SampleSheet() = %v, want %v", got, want)
	}
}

func TestQuickTag(t *testing.T) {
	if got := QuickTag(lavender()); got != "Lavender (Lavandula angustifolia) [FS] $8.25" {
		t.Errorf("QuickTag() = %q", got)
	}
	if got := QuickTag(Tag{}); got != "" {
		t.Errorf("QuickTag on a bad tag = %q, want empty", got)
	}
}
