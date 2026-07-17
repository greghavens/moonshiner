package shardplan

import (
	"fmt"
	"reflect"
	"strings"
	"testing"
)

func mustPlan(t *testing.T, current map[string][]string, nodes []string, replicas int) *Plan {
	t.Helper()
	p, err := Rebalance(current, nodes, replicas)
	if err != nil {
		t.Fatalf("Rebalance: %v", err)
	}
	if p.Moves == nil {
		t.Fatal("Moves must be non-nil")
	}
	return p
}

func TestAddNodeSingleReplica(t *testing.T) {
	current := map[string][]string{
		"s1": {"a"}, "s2": {"a"}, "s3": {"a"},
		"s4": {"b"}, "s5": {"b"}, "s6": {"b"},
	}
	p := mustPlan(t, current, []string{"a", "b", "c"}, 1)

	wantMoves := []Move{
		{Shard: "s1", Replica: 0, From: "a", To: "c"},
		{Shard: "s4", Replica: 0, From: "b", To: "c"},
	}
	if !reflect.DeepEqual(p.Moves, wantMoves) {
		t.Fatalf("Moves = %+v, want %+v", p.Moves, wantMoves)
	}
	wantTarget := map[string][]string{
		"s1": {"c"}, "s2": {"a"}, "s3": {"a"},
		"s4": {"c"}, "s5": {"b"}, "s6": {"b"},
	}
	if !reflect.DeepEqual(p.Target, wantTarget) {
		t.Fatalf("Target = %v, want %v", p.Target, wantTarget)
	}
	if err := Check(p.Target, []string{"a", "b", "c"}, 1); err != nil {
		t.Fatalf("Check on planned target: %v", err)
	}
	wantDiff := []string{"s1/0: a -> c", "s4/0: b -> c"}
	if !reflect.DeepEqual(p.Diff(), wantDiff) {
		t.Fatalf("Diff = %v, want %v", p.Diff(), wantDiff)
	}
}

func TestRemoveNodeMovesOnlyItsReplicas(t *testing.T) {
	current := map[string][]string{
		"s1": {"a"}, "s2": {"a"}, "s3": {"b"},
		"s4": {"b"}, "s5": {"c"}, "s6": {"c"},
	}
	p := mustPlan(t, current, []string{"a", "b"}, 1)

	wantMoves := []Move{
		{Shard: "s5", Replica: 0, From: "c", To: "a"},
		{Shard: "s6", Replica: 0, From: "c", To: "b"},
	}
	if !reflect.DeepEqual(p.Moves, wantMoves) {
		t.Fatalf("Moves = %+v, want %+v", p.Moves, wantMoves)
	}
	// shards that never lived on the drained node stay put
	for _, s := range []string{"s1", "s2", "s3", "s4"} {
		if !reflect.DeepEqual(p.Target[s], current[s]) {
			t.Fatalf("shard %s moved needlessly: %v", s, p.Target[s])
		}
	}
	if err := Check(p.Target, []string{"a", "b"}, 1); err != nil {
		t.Fatalf("Check: %v", err)
	}
}

func TestAddNodeWithReplicasMinimalMove(t *testing.T) {
	current := map[string][]string{
		"x": {"a", "b"},
		"y": {"b", "c"},
		"z": {"c", "a"},
	}
	nodes := []string{"a", "b", "c", "d"}
	p := mustPlan(t, current, nodes, 2)

	wantMoves := []Move{{Shard: "y", Replica: 1, From: "c", To: "d"}}
	if !reflect.DeepEqual(p.Moves, wantMoves) {
		t.Fatalf("Moves = %+v, want %+v", p.Moves, wantMoves)
	}
	if !reflect.DeepEqual(p.Diff(), []string{"y/1: c -> d"}) {
		t.Fatalf("Diff = %v", p.Diff())
	}
	if err := Check(p.Target, nodes, 2); err != nil {
		t.Fatalf("Check: %v", err)
	}

	// planning the plan's own target is a no-op (idempotent apply retries)
	p2 := mustPlan(t, p.Target, nodes, 2)
	if len(p2.Moves) != 0 {
		t.Fatalf("re-plan of a clean target moved things: %+v", p2.Moves)
	}
}

func TestNodeArgumentOrderDoesNotMatter(t *testing.T) {
	current := map[string][]string{
		"x": {"a", "b"},
		"y": {"b", "c"},
		"z": {"c", "a"},
	}
	p1 := mustPlan(t, current, []string{"a", "b", "c", "d"}, 2)
	p2 := mustPlan(t, current, []string{"d", "c", "b", "a"}, 2)
	if !reflect.DeepEqual(p1, p2) {
		t.Fatalf("plans differ by node order:\n%+v\n%+v", p1, p2)
	}
}

func TestRepairsDuplicateNodeWithinShard(t *testing.T) {
	current := map[string][]string{"w": {"a", "a"}}
	p := mustPlan(t, current, []string{"a", "b"}, 2)
	wantMoves := []Move{{Shard: "w", Replica: 1, From: "a", To: "b"}}
	if !reflect.DeepEqual(p.Moves, wantMoves) {
		t.Fatalf("Moves = %+v, want %+v", p.Moves, wantMoves)
	}
	if !reflect.DeepEqual(p.Target["w"], []string{"a", "b"}) {
		t.Fatalf("Target[w] = %v", p.Target["w"])
	}
}

func TestPlacesNewShardOnLeastLoadedNode(t *testing.T) {
	current := map[string][]string{"s1": {"a"}, "s7": {}}
	p := mustPlan(t, current, []string{"a", "b"}, 1)
	wantMoves := []Move{{Shard: "s7", Replica: 0, From: "", To: "b"}}
	if !reflect.DeepEqual(p.Moves, wantMoves) {
		t.Fatalf("Moves = %+v, want %+v", p.Moves, wantMoves)
	}
	if !reflect.DeepEqual(p.Diff(), []string{"s7/0: + b"}) {
		t.Fatalf("Diff = %v", p.Diff())
	}
}

func TestReplicaGrowth(t *testing.T) {
	current := map[string][]string{"x": {"a"}, "y": {"b"}}
	p := mustPlan(t, current, []string{"a", "b"}, 2)
	wantMoves := []Move{
		{Shard: "x", Replica: 1, From: "", To: "b"},
		{Shard: "y", Replica: 1, From: "", To: "a"},
	}
	if !reflect.DeepEqual(p.Moves, wantMoves) {
		t.Fatalf("Moves = %+v, want %+v", p.Moves, wantMoves)
	}
	if err := Check(p.Target, []string{"a", "b"}, 2); err != nil {
		t.Fatalf("Check: %v", err)
	}
}

func TestReplicaShrinkDropsHighSlots(t *testing.T) {
	current := map[string][]string{"x": {"a", "b"}}
	p := mustPlan(t, current, []string{"a", "b"}, 1)
	wantMoves := []Move{{Shard: "x", Replica: 1, From: "b", To: ""}}
	if !reflect.DeepEqual(p.Moves, wantMoves) {
		t.Fatalf("Moves = %+v, want %+v", p.Moves, wantMoves)
	}
	if !reflect.DeepEqual(p.Diff(), []string{"x/1: - b"}) {
		t.Fatalf("Diff = %v", p.Diff())
	}
	if !reflect.DeepEqual(p.Target["x"], []string{"a"}) {
		t.Fatalf("Target[x] = %v", p.Target["x"])
	}
}

func TestUnsatisfiableAntiAffinityFailsLoudly(t *testing.T) {
	current := map[string][]string{
		"k": {"a", "b"},
		"l": {"a", "b"},
		"m": {"c"},
	}
	_, err := Rebalance(current, []string{"a", "b", "c"}, 2)
	if err == nil {
		t.Fatal("expected an error, got a plan")
	}
	if got, want := err.Error(), `cannot place shard "m" replica 1`; got != want {
		t.Fatalf("error = %q, want %q", got, want)
	}
}

func TestValidation(t *testing.T) {
	current := map[string][]string{"x": {"a"}}
	cases := []struct {
		name     string
		nodes    []string
		replicas int
	}{
		{"replicas below one", []string{"a", "b"}, 0},
		{"more replicas than nodes", []string{"a", "b"}, 3},
		{"duplicate node names", []string{"a", "a"}, 1},
		{"empty node name", []string{"a", ""}, 1},
	}
	for _, tc := range cases {
		if _, err := Rebalance(current, tc.nodes, tc.replicas); err == nil {
			t.Errorf("%s: expected an error", tc.name)
		}
	}
}

func TestCheck(t *testing.T) {
	nodes := []string{"a", "b"}
	if err := Check(map[string][]string{"x": {"a"}, "y": {"b"}}, nodes, 1); err != nil {
		t.Fatalf("clean assignment flagged: %v", err)
	}
	if err := Check(map[string][]string{"x": {"a"}}, nodes, 2); err == nil ||
		!strings.Contains(err.Error(), "x") {
		t.Fatalf("wrong slot count must name the shard, got %v", err)
	}
	if err := Check(map[string][]string{"x": {"a", "z"}}, nodes, 2); err == nil ||
		!strings.Contains(err.Error(), "z") {
		t.Fatalf("unknown node must be named, got %v", err)
	}
	if err := Check(map[string][]string{"x": {"a", "a"}}, nodes, 2); err == nil ||
		!strings.Contains(err.Error(), "x") {
		t.Fatalf("anti-affinity violation must name the shard, got %v", err)
	}
	// spread: a carries 2, b carries 0
	if err := Check(map[string][]string{"x": {"a"}, "y": {"a"}}, nodes, 1); err == nil {
		t.Fatal("load spread above 1 must be flagged")
	}
}

func TestBulkAddNodeBounds(t *testing.T) {
	// 40 shards x 2 replicas striped over 5 nodes, 16 slots per node
	nodes5 := []string{"n1", "n2", "n3", "n4", "n5"}
	current := map[string][]string{}
	for i := 0; i < 40; i++ {
		shard := fmt.Sprintf("s%02d", i+1)
		current[shard] = []string{nodes5[(2*i)%5], nodes5[(2*i+1)%5]}
	}
	nodes6 := append(append([]string{}, nodes5...), "n6")
	p := mustPlan(t, current, nodes6, 2)

	// 80 slots over 6 nodes → n6 must end at 13; minimal plan is exactly 13
	// moves, every single one INTO the new node
	if len(p.Moves) != 13 {
		t.Fatalf("len(Moves) = %d, want 13\n%v", len(p.Moves), p.Diff())
	}
	movedShards := map[string]bool{}
	for _, m := range p.Moves {
		if m.To != "n6" {
			t.Fatalf("move %+v does not target the new node", m)
		}
		if m.From == "" || m.From == "n6" {
			t.Fatalf("move %+v has a bogus source", m)
		}
		if movedShards[m.Shard] {
			t.Fatalf("shard %s loses two replicas in one plan", m.Shard)
		}
		movedShards[m.Shard] = true
		// only the planned slot changed; the sibling replica stays put
		other := 1 - m.Replica
		if p.Target[m.Shard][m.Replica] != "n6" ||
			p.Target[m.Shard][other] != current[m.Shard][other] {
			t.Fatalf("shard %s rewritten beyond its move: %v (was %v)",
				m.Shard, p.Target[m.Shard], current[m.Shard])
		}
	}
	for shard, slots := range current {
		if !movedShards[shard] && !reflect.DeepEqual(p.Target[shard], slots) {
			t.Fatalf("unmoved shard %s changed: %v (was %v)", shard, p.Target[shard], slots)
		}
	}
	if err := Check(p.Target, nodes6, 2); err != nil {
		t.Fatalf("Check: %v", err)
	}
	if len(p.Diff()) != 13 {
		t.Fatalf("Diff length = %d", len(p.Diff()))
	}

	// byte-identical determinism, including across node order
	p2 := mustPlan(t, current, []string{"n6", "n3", "n1", "n5", "n2", "n4"}, 2)
	if !reflect.DeepEqual(p, p2) {
		t.Fatal("plan is not deterministic across node argument order")
	}
}
