package cidrkit

import (
	"reflect"
	"testing"
)

// Acceptance contract for the new range operations: Merge, Net.Split,
// and Exclude. Results are canonical: sorted by network address, and
// always the minimal set of CIDR blocks covering exactly the right
// addresses.

func nets(t *testing.T, specs ...string) []Net {
	t.Helper()
	var out []Net
	for _, s := range specs {
		n, err := ParseCIDR(s)
		if err != nil {
			t.Fatalf("ParseCIDR(%q): %v", s, err)
		}
		out = append(out, n)
	}
	return out
}

func strs(ns []Net) []string {
	var out []string
	for _, n := range ns {
		out = append(out, n.String())
	}
	return out
}

func TestMergeCombinesSiblingsAndOverlaps(t *testing.T) {
	cases := []struct {
		name string
		in   []string
		want []string
	}{
		{"two halves become the parent",
			[]string{"10.0.0.0/25", "10.0.0.128/25"},
			[]string{"10.0.0.0/24"}},
		{"aligned adjacent /24s become a /23",
			[]string{"10.0.1.0/24", "10.0.0.0/24"},
			[]string{"10.0.0.0/23"}},
		{"adjacent but unaligned blocks stay separate",
			[]string{"10.0.2.0/24", "10.0.1.0/24"},
			[]string{"10.0.1.0/24", "10.0.2.0/24"}},
		{"contained block disappears",
			[]string{"10.0.0.64/26", "10.0.0.0/24"},
			[]string{"10.0.0.0/24"}},
		{"duplicates collapse",
			[]string{"192.168.1.0/24", "192.168.1.0/24"},
			[]string{"192.168.1.0/24"}},
		{"four scattered /24s collapse to a /22",
			[]string{"10.0.2.0/24", "10.0.0.0/24", "10.0.3.0/24", "10.0.1.0/24"},
			[]string{"10.0.0.0/22"}},
		{"cascade: quarter plus two eighths become the whole /24",
			[]string{"10.0.0.0/25", "10.0.0.128/26", "10.0.0.192/26"},
			[]string{"10.0.0.0/24"}},
		{"disjoint blocks come back sorted",
			[]string{"172.16.0.0/16", "10.0.0.0/8"},
			[]string{"10.0.0.0/8", "172.16.0.0/16"}},
	}
	for _, c := range cases {
		got := strs(Merge(nets(t, c.in...)))
		if !reflect.DeepEqual(got, c.want) {
			t.Fatalf("%s: Merge(%v) = %v, want %v", c.name, c.in, got, c.want)
		}
	}
}

func TestMergeOfNothingIsNothing(t *testing.T) {
	if got := Merge(nil); len(got) != 0 {
		t.Fatalf("Merge(nil) = %v, want empty", strs(got))
	}
}

func TestSplitIntoEqualBlocks(t *testing.T) {
	n := nets(t, "10.0.0.0/24")[0]
	got, err := n.Split(4)
	if err != nil {
		t.Fatalf("Split(4): %v", err)
	}
	want := []string{"10.0.0.0/26", "10.0.0.64/26", "10.0.0.128/26", "10.0.0.192/26"}
	if !reflect.DeepEqual(strs(got), want) {
		t.Fatalf("Split(4) = %v, want %v", strs(got), want)
	}
}

func TestSplitByOneReturnsTheBlockItself(t *testing.T) {
	n := nets(t, "10.9.0.0/16")[0]
	got, err := n.Split(1)
	if err != nil {
		t.Fatalf("Split(1): %v", err)
	}
	if !reflect.DeepEqual(strs(got), []string{"10.9.0.0/16"}) {
		t.Fatalf("Split(1) = %v", strs(got))
	}
}

func TestSplitDownToSingleAddresses(t *testing.T) {
	n := nets(t, "10.0.0.0/31")[0]
	got, err := n.Split(2)
	if err != nil {
		t.Fatalf("Split(2): %v", err)
	}
	if !reflect.DeepEqual(strs(got), []string{"10.0.0.0/32", "10.0.0.1/32"}) {
		t.Fatalf("Split(2) = %v", strs(got))
	}
}

func TestSplitTheWholeInternetInHalf(t *testing.T) {
	n := nets(t, "0.0.0.0/0")[0]
	got, err := n.Split(2)
	if err != nil {
		t.Fatalf("Split(2): %v", err)
	}
	if !reflect.DeepEqual(strs(got), []string{"0.0.0.0/1", "128.0.0.0/1"}) {
		t.Fatalf("Split(2) = %v", strs(got))
	}
}

func TestSplitRejectsImpossibleCounts(t *testing.T) {
	n := nets(t, "10.0.0.0/24")[0]
	for _, parts := range []int{0, -2, 3, 6, 100} {
		if _, err := n.Split(parts); err == nil {
			t.Fatalf("Split(%d) succeeded, want error (not a positive power of two)", parts)
		}
	}
	single := nets(t, "10.0.0.4/32")[0]
	if _, err := single.Split(2); err == nil {
		t.Fatal("Split(2) of a /32 succeeded, want error (block too small)")
	}
	if _, err := n.Split(512); err == nil {
		t.Fatal("Split(512) of a /24 succeeded, want error (block too small)")
	}
}

func TestExcludeCarvesHolesMinimally(t *testing.T) {
	outer := nets(t, "10.0.0.0/24")[0]
	cases := []struct {
		name  string
		holes []string
		want  []string
	}{
		{"leading quarter removed",
			[]string{"10.0.0.0/26"},
			[]string{"10.0.0.64/26", "10.0.0.128/25"}},
		{"middle slice removed",
			[]string{"10.0.0.128/26"},
			[]string{"10.0.0.0/25", "10.0.0.192/26"}},
		{"no holes returns the block",
			nil,
			[]string{"10.0.0.0/24"}},
		{"hole entirely outside is ignored",
			[]string{"192.168.0.0/16"},
			[]string{"10.0.0.0/24"}},
		{"overlapping holes counted once",
			[]string{"10.0.0.0/25", "10.0.0.64/26", "10.0.0.240/28"},
			[]string{"10.0.0.128/26", "10.0.0.192/27", "10.0.0.224/28"}},
	}
	for _, c := range cases {
		got := strs(Exclude(outer, nets(t, c.holes...)))
		if !reflect.DeepEqual(got, c.want) {
			t.Fatalf("%s: Exclude = %v, want %v", c.name, got, c.want)
		}
	}
}

func TestExcludeSingleAddressHole(t *testing.T) {
	outer := nets(t, "10.0.0.0/30")[0]
	got := strs(Exclude(outer, nets(t, "10.0.0.2/32")))
	want := []string{"10.0.0.0/31", "10.0.0.3/32"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Exclude = %v, want %v", got, want)
	}
}

func TestExcludeEverythingLeavesNothing(t *testing.T) {
	outer := nets(t, "10.0.0.0/24")[0]
	if got := Exclude(outer, nets(t, "10.0.0.0/16")); len(got) != 0 {
		t.Fatalf("Exclude = %v, want empty", strs(got))
	}
	if got := Exclude(outer, nets(t, "10.0.0.0/25", "10.0.0.128/25")); len(got) != 0 {
		t.Fatalf("Exclude with two half holes = %v, want empty", strs(got))
	}
}
