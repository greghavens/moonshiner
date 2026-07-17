package modcycles

import (
	"strings"
	"testing"
)

func equalCycles(a, b [][]string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if !equal(a[i], b[i]) {
			return false
		}
	}
	return true
}

func TestAcyclicGraphHasNoCyclesAndTopoOrders(t *testing.T) {
	root := writeTree(t, map[string]string{
		"app.mod":  "module app\nimport lib\nimport util\n",
		"lib.mod":  "module lib\nimport util\n",
		"util.mod": "module util\n",
		"zed.mod":  "module zed\n",
	})
	g, err := Load(root)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cycles := g.Cycles(); len(cycles) != 0 {
		t.Fatalf("Cycles() = %v, want none", cycles)
	}
	order, err := g.Order()
	if err != nil {
		t.Fatalf("Order: %v", err)
	}
	// dependencies first; ties broken alphabetically at every step
	if !equal(order, []string{"util", "lib", "app", "zed"}) {
		t.Errorf("Order() = %v, want [util lib app zed]", order)
	}
}

func TestSimpleCycleIsFoundAndNormalized(t *testing.T) {
	// declared so the walk meets m before k: the cycle must still be
	// rotated to start at its alphabetically smallest member
	root := writeTree(t, map[string]string{
		"m.mod": "module m\nimport k\n",
		"k.mod": "module k\nimport m\n",
	})
	g, err := Load(root)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	want := [][]string{{"k", "m"}}
	if got := g.Cycles(); !equalCycles(got, want) {
		t.Errorf("Cycles() = %v, want %v", got, want)
	}
	if _, err := g.Order(); err == nil {
		t.Error("Order() must fail while a cycle exists")
	} else if !strings.Contains(err.Error(), "cycle") {
		t.Errorf("Order() error %q should mention a cycle", err)
	}
}

func TestSelfImportIsACycleOfOne(t *testing.T) {
	root := writeTree(t, map[string]string{
		"x.mod": "module x\nimport x\n",
	})
	g, err := Load(root)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	want := [][]string{{"x"}}
	if got := g.Cycles(); !equalCycles(got, want) {
		t.Errorf("Cycles() = %v, want %v", got, want)
	}
}

func TestAllElementaryCyclesAreEnumerated(t *testing.T) {
	// a->b, a->c, b->a, b->c, c->b holds three elementary cycles:
	// a->b->a, a->c->b->a, b->c->b
	root := writeTree(t, map[string]string{
		"a.mod": "module a\nimport b\nimport c\n",
		"b.mod": "module b\nimport a\nimport c\n",
		"c.mod": "module c\nimport b\n",
	})
	g, err := Load(root)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	want := [][]string{{"a", "b"}, {"a", "c", "b"}, {"b", "c"}}
	if got := g.Cycles(); !equalCycles(got, want) {
		t.Errorf("Cycles() = %v, want %v", got, want)
	}
}

func TestDisjointCyclesReportedInOrder(t *testing.T) {
	root := writeTree(t, map[string]string{
		"pair/a.mod": "module a\nimport b\n",
		"pair/b.mod": "module b\nimport a\n",
		"trio/c.mod": "module c\nimport d\n",
		"trio/d.mod": "module d\nimport e\n",
		"trio/e.mod": "module e\nimport c\n",
		"free.mod":   "module free\n",
	})
	g, err := Load(root)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	want := [][]string{{"a", "b"}, {"c", "d", "e"}}
	if got := g.Cycles(); !equalCycles(got, want) {
		t.Errorf("Cycles() = %v, want %v", got, want)
	}
}

func TestCycleWalkFollowsEdgeDirection(t *testing.T) {
	// frontend -> api -> auth -> frontend; billing hangs off api harmlessly
	root := writeTree(t, map[string]string{
		"ui/frontend.mod": "module frontend\nimport api\n",
		"svc/api.mod":     "module api\nimport auth\n",
		"svc/auth.mod":    "module auth\nimport frontend\n",
		"billing.mod":     "module billing\nimport api\n",
	})
	g, err := Load(root)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	// starting at the smallest member, each element must import the next
	want := [][]string{{"api", "auth", "frontend"}}
	if got := g.Cycles(); !equalCycles(got, want) {
		t.Errorf("Cycles() = %v, want %v", got, want)
	}
}

func TestMissingImportsNeverFormCyclesNorBlockOrder(t *testing.T) {
	root := writeTree(t, map[string]string{
		"a.mod": "module a\nimport ghost\n",
		"b.mod": "module b\nimport a\n",
	})
	g, err := Load(root)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if cycles := g.Cycles(); len(cycles) != 0 {
		t.Errorf("Cycles() = %v, want none", cycles)
	}
	if got := g.Missing(); !equal(got, []string{"ghost"}) {
		t.Errorf("Missing() = %v, want [ghost]", got)
	}
	order, err := g.Order()
	if err != nil {
		t.Fatalf("Order: %v", err)
	}
	if !equal(order, []string{"a", "b"}) {
		t.Errorf("Order() = %v, want [a b]", order)
	}
}
