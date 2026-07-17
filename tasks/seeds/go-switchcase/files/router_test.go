// Regression tests for the order router — protected file.
//
// The routing document is a workflow-DSL switch task: cases are checked
// top to bottom, the FIRST case whose condition holds routes the item,
// and the case without a condition is the default, used only when no
// other case matches.
package router

import (
	"strings"
	"testing"
)

const rulesYAML = `document:
  dsl: "1.0"
  namespace: fulfilment
  name: order-routing
route:
  switch:
    - when: .rush
      then: expedite
    - when: .total >= 100
      then: bulk
    - then: standard
`

// newRouter loads rulesYAML and registers a logging handler per queue.
func newRouter(t *testing.T) (*Router, *[]string) {
	t.Helper()
	rs, err := LoadRules([]byte(rulesYAML))
	if err != nil {
		t.Fatalf("LoadRules: %v", err)
	}
	r := NewRouter(rs)
	log := &[]string{}
	for _, q := range []string{"expedite", "bulk", "standard"} {
		queue := q
		r.Handle(queue, func(map[string]any) { *log = append(*log, queue) })
	}
	return r, log
}

// ----------------------------------------------------------------- loading

func TestLoadRules(t *testing.T) {
	rs, err := LoadRules([]byte(rulesYAML))
	if err != nil {
		t.Fatalf("LoadRules: %v", err)
	}
	if rs.Document.Namespace != "fulfilment" || rs.Document.Name != "order-routing" {
		t.Fatalf("document = %+v", rs.Document)
	}
	if len(rs.Rules) != 3 {
		t.Fatalf("len(rules) = %d, want 3", len(rs.Rules))
	}
	if rs.Rules[0].When != ".rush" || rs.Rules[0].Then != "expedite" {
		t.Fatalf("rule 0 = %+v", rs.Rules[0])
	}
	if rs.Rules[2].When != "" || rs.Rules[2].Then != "standard" {
		t.Fatalf("rule 2 = %+v", rs.Rules[2])
	}
}

func TestLoadRulesRejectsEmptySwitch(t *testing.T) {
	_, err := LoadRules([]byte("route:\n  switch: []\n"))
	if err == nil || !strings.Contains(err.Error(), "no switch cases") {
		t.Fatalf("err = %v, want it to mention missing switch cases", err)
	}
}

// -------------------------------------------------------------- dispatching

func TestFirstMatchingRuleWins(t *testing.T) {
	r, log := newRouter(t)
	dest, err := r.Dispatch(map[string]any{"rush": true, "total": 250})
	if err != nil {
		t.Fatalf("Dispatch: %v", err)
	}
	if dest != "expedite" {
		t.Fatalf("dest = %q, want %q", dest, "expedite")
	}
	if len(*log) != 1 || (*log)[0] != "expedite" {
		t.Fatalf("handlers fired: %v, want exactly [expedite]", *log)
	}
}

func TestDefaultStaysOutOfTheWayWhenARuleMatches(t *testing.T) {
	r, log := newRouter(t)
	dest, err := r.Dispatch(map[string]any{"rush": false, "total": 500})
	if err != nil {
		t.Fatalf("Dispatch: %v", err)
	}
	if dest != "bulk" {
		t.Fatalf("dest = %q, want %q", dest, "bulk")
	}
	if len(*log) != 1 || (*log)[0] != "bulk" {
		t.Fatalf("handlers fired: %v, want exactly [bulk]", *log)
	}
}

func TestDefaultCatchesUnmatchedItems(t *testing.T) {
	r, log := newRouter(t)
	dest, err := r.Dispatch(map[string]any{"rush": false, "total": 10})
	if err != nil {
		t.Fatalf("Dispatch: %v", err)
	}
	if dest != "standard" {
		t.Fatalf("dest = %q, want %q", dest, "standard")
	}
	if len(*log) != 1 || (*log)[0] != "standard" {
		t.Fatalf("handlers fired: %v, want exactly [standard]", *log)
	}
}

func TestMatchedRouteReturnsBeforeLaterConditionsRun(t *testing.T) {
	// The item has no total field at all; if dispatch kept going past the
	// matched first rule, the second condition would evaluate against a
	// missing field. A matched route must return without looking further.
	rs, err := LoadRules([]byte(rulesYAML))
	if err != nil {
		t.Fatalf("LoadRules: %v", err)
	}
	r := NewRouter(rs) // no handlers registered: routing alone still works
	dest, err := r.Dispatch(map[string]any{"rush": true})
	if err != nil {
		t.Fatalf("Dispatch: %v", err)
	}
	if dest != "expedite" {
		t.Fatalf("dest = %q, want %q", dest, "expedite")
	}
}

func TestNoRouteMatched(t *testing.T) {
	rs, err := LoadRules([]byte("route:\n  switch:\n    - when: .rush\n      then: expedite\n"))
	if err != nil {
		t.Fatalf("LoadRules: %v", err)
	}
	r := NewRouter(rs)
	_, err = r.Dispatch(map[string]any{"total": 5})
	if err == nil || !strings.Contains(err.Error(), "no route matched") {
		t.Fatalf("err = %v, want no route matched", err)
	}
}

func TestConditionErrorsPropagate(t *testing.T) {
	rs, err := LoadRules([]byte("route:\n  switch:\n    - when: total >= 100\n      then: bulk\n"))
	if err != nil {
		t.Fatalf("LoadRules: %v", err)
	}
	r := NewRouter(rs)
	_, err = r.Dispatch(map[string]any{"total": 500})
	if err == nil || !strings.Contains(err.Error(), "must start with") {
		t.Fatalf("err = %v, want a condition path error", err)
	}
}

// --------------------------------------------------------------- conditions

func TestEvalConditions(t *testing.T) {
	cases := []struct {
		cond string
		item map[string]any
		want bool
	}{
		{".rush", map[string]any{"rush": true}, true},
		{".rush", map[string]any{"rush": false}, false},
		{".rush", map[string]any{}, false},
		{".note", map[string]any{"note": ""}, false},
		{".note", map[string]any{"note": "x"}, true},
		{".count", map[string]any{"count": 0}, false},
		{".total >= 100", map[string]any{"total": 100}, true},
		{".total > 100", map[string]any{"total": 100}, false},
		{".total < 20.5", map[string]any{"total": 20}, true},
		{".total != 3", map[string]any{"total": 3}, false},
		{".total == 42", map[string]any{"total": 42.0}, true},
		{`.customer.tier == "gold"`, map[string]any{"customer": map[string]any{"tier": "gold"}}, true},
		{`.customer.tier != "gold"`, map[string]any{"customer": map[string]any{"tier": "silver"}}, true},
		{".flag == true", map[string]any{"flag": true}, true},
	}
	for _, tc := range cases {
		got, err := evalCond(tc.cond, tc.item)
		if err != nil {
			t.Errorf("evalCond(%q): %v", tc.cond, err)
			continue
		}
		if got != tc.want {
			t.Errorf("evalCond(%q, %v) = %v, want %v", tc.cond, tc.item, got, tc.want)
		}
	}
}

func TestEvalConditionErrors(t *testing.T) {
	for _, cond := range []string{
		"total >= 100",
		".a ~ 1",
		".a == gold",
		".a == 1 2",
	} {
		if _, err := evalCond(cond, map[string]any{"a": 1, "total": 1}); err == nil {
			t.Errorf("evalCond(%q) succeeded, want an error", cond)
		}
	}
}
