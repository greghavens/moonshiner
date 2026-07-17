package reviewflag

import (
	"reflect"
	"testing"
)

var weekReviews = []string{
	"Asked for a refund after the hinge arrived broken.",
	"Second unit also broken. Refund process was slow and the box had a scratch.",
	"Delivery was late, but the base plate was fine otherwise.",
	"The prefunded gift card option is unrelated, no complaints there.",
	"Unbroken seal on the replacement, no scratches on mine.",
}

func TestSummaryCountsWholeWordsCaseInsensitively(t *testing.T) {
	s := BuildSummary("hinge-kit", weekReviews)
	want := []Flag{
		{Term: "broken", Severity: "high", Count: 2},
		{Term: "refund", Severity: "high", Count: 2},
		{Term: "late", Severity: "medium", Count: 1},
		{Term: "scratch", Severity: "low", Count: 1},
	}
	if !reflect.DeepEqual(s.Flags, want) {
		t.Fatalf("flags = %#v, want %#v", s.Flags, want)
	}
	if s.Total != 6 {
		t.Fatalf("total = %d, want 6", s.Total)
	}
}

func TestTermInsideALongerWordDoesNotCount(t *testing.T) {
	s := BuildSummary("gift-card", []string{
		"I prefunded the account and the plate is unbroken.",
	})
	if len(s.Flags) != 0 || s.Total != 0 {
		t.Fatalf("expected no flags for embedded terms, got %#v (total %d)", s.Flags, s.Total)
	}
}

func TestExportMatchesDashboardSchema(t *testing.T) {
	out, err := Export(BuildSummary("hinge-kit", weekReviews))
	if err != nil {
		t.Fatalf("export: %v", err)
	}
	want := `{"product":"hinge-kit","total_matches":6,"flags":[` +
		`{"term":"broken","severity":"high","count":2},` +
		`{"term":"refund","severity":"high","count":2},` +
		`{"term":"late","severity":"medium","count":1},` +
		`{"term":"scratch","severity":"low","count":1}]}`
	if out != want {
		t.Fatalf("export document:\n got %s\nwant %s", out, want)
	}
}

func TestExportOfQuietWeek(t *testing.T) {
	out, err := Export(BuildSummary("quiet-week", nil))
	if err != nil {
		t.Fatalf("export: %v", err)
	}
	want := `{"product":"quiet-week","total_matches":0,"flags":null}`
	if out != want {
		t.Fatalf("export document = %s, want %s", out, want)
	}
}
