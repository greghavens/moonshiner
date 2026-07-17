package trailperm

import "testing"

func TestCatalogIsAlphabetizedCopy(t *testing.T) {
	got := Catalog()
	want := []string{"Larch Basin", "Ridgeway", "Slate Cirque"}
	if len(got) != len(want) {
		t.Fatalf("Catalog() returned %d zones, want %d", len(got), len(want))
	}
	for i, name := range want {
		if got[i].Name != name {
			t.Fatalf("Catalog()[%d].Name = %q, want %q", i, got[i].Name, name)
		}
	}
	if District[0].ID != "RW" || District[1].ID != "LB" || District[2].ID != "SC" {
		t.Fatalf("District order changed: %v", District)
	}
}

func TestRemainingClampsAtZero(t *testing.T) {
	z := Zone{ID: "LB", Name: "Larch Basin", Quota: 12}
	cases := []struct {
		issued, want int
	}{
		{0, 12},
		{5, 7},
		{12, 0},
		{15, 0},
	}
	for _, c := range cases {
		if got := Remaining(z, c.issued); got != c.want {
			t.Errorf("Remaining(quota=12, issued=%d) = %d, want %d", c.issued, got, c.want)
		}
	}
}

func TestCanIssue(t *testing.T) {
	z := Zone{ID: "SC", Name: "Slate Cirque", Quota: 8}
	if !CanIssue(z, 5, 3) {
		t.Error("party of 3 with 3 left should be issuable")
	}
	if CanIssue(z, 5, 4) {
		t.Error("party of 4 with 3 left should be refused")
	}
	if CanIssue(z, 0, 0) {
		t.Error("empty party should be refused")
	}
}

func TestBoardRendersInGivenOrder(t *testing.T) {
	issued := map[string]int{"RW": 4, "LB": 12, "SC": 9}
	got := Board(Catalog(), issued)
	want := "Larch Basin: 0/12\nRidgeway: 26/30\nSlate Cirque: 0/8\n"
	if got != want {
		t.Fatalf("Board() =\n%q\nwant\n%q", got, want)
	}
}
