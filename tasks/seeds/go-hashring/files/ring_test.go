package ring

import (
	"errors"
	"fmt"
	"reflect"
	"sort"
	"testing"
)

func newRing(t *testing.T, vnodes int, nodes ...string) *Ring {
	t.Helper()
	r, err := New(vnodes)
	if err != nil {
		t.Fatalf("New(%d): unexpected error %v", vnodes, err)
	}
	for _, n := range nodes {
		if err := r.AddNode(n); err != nil {
			t.Fatalf("AddNode(%q): unexpected error %v", n, err)
		}
	}
	return r
}

func mapKeys(t *testing.T, r *Ring, n int) map[string]string {
	t.Helper()
	out := make(map[string]string, n)
	for i := 0; i < n; i++ {
		key := fmt.Sprintf("session:%05d", i)
		node, err := r.Get(key)
		if err != nil {
			t.Fatalf("Get(%q): unexpected error %v", key, err)
		}
		out[key] = node
	}
	return out
}

func TestNewValidation(t *testing.T) {
	for _, v := range []int{0, -1, -128} {
		if _, err := New(v); err == nil {
			t.Errorf("New(%d) = nil error, want non-nil", v)
		}
	}
	if _, err := New(1); err != nil {
		t.Errorf("New(1): unexpected error %v", err)
	}
}

func TestEmptyRing(t *testing.T) {
	r := newRing(t, 32)
	if _, err := r.Get("anything"); !errors.Is(err, ErrEmptyRing) {
		t.Errorf("Get on empty ring: error = %v, want ErrEmptyRing", err)
	}
	if nodes := r.Nodes(); len(nodes) != 0 {
		t.Errorf("Nodes() on empty ring = %v, want empty", nodes)
	}
}

func TestSingleNodeGetsEverything(t *testing.T) {
	r := newRing(t, 16, "cache-a.internal")
	for i := 0; i < 50; i++ {
		key := fmt.Sprintf("obj-%d", i)
		node, err := r.Get(key)
		if err != nil {
			t.Fatalf("Get(%q): unexpected error %v", key, err)
		}
		if node != "cache-a.internal" {
			t.Errorf("Get(%q) = %q, want the only node", key, node)
		}
	}
}

func TestDuplicateAndUnknownNodes(t *testing.T) {
	r := newRing(t, 32, "cache-a.internal", "cache-b.internal")
	before := mapKeys(t, r, 200)

	if err := r.AddNode("cache-a.internal"); !errors.Is(err, ErrNodeExists) {
		t.Errorf("AddNode of existing node: error = %v, want ErrNodeExists", err)
	}
	if err := r.RemoveNode("cache-z.internal"); !errors.Is(err, ErrUnknownNode) {
		t.Errorf("RemoveNode of unknown node: error = %v, want ErrUnknownNode", err)
	}

	// Failed operations must leave placement untouched.
	if after := mapKeys(t, r, 200); !reflect.DeepEqual(before, after) {
		t.Errorf("failed AddNode/RemoveNode changed key placement")
	}
}

func TestNodesSortedAndStable(t *testing.T) {
	r := newRing(t, 8, "web-3", "web-1", "web-2")
	want := []string{"web-1", "web-2", "web-3"}
	got := r.Nodes()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Nodes() = %v, want sorted %v", got, want)
	}
	if again := r.Nodes(); !reflect.DeepEqual(again, want) {
		t.Fatalf("Nodes() second call = %v, want %v", again, want)
	}
	if err := r.RemoveNode("web-2"); err != nil {
		t.Fatalf("RemoveNode: %v", err)
	}
	if got := r.Nodes(); !reflect.DeepEqual(got, []string{"web-1", "web-3"}) {
		t.Fatalf("Nodes() after removal = %v, want [web-1 web-3]", got)
	}
}

func TestPlacementIgnoresInsertionOrder(t *testing.T) {
	a := newRing(t, 64, "cache-a.internal", "cache-b.internal", "cache-c.internal")
	b := newRing(t, 64, "cache-c.internal", "cache-a.internal", "cache-b.internal")
	ma := mapKeys(t, a, 500)
	mb := mapKeys(t, b, 500)
	if !reflect.DeepEqual(ma, mb) {
		diff := 0
		for k, v := range ma {
			if mb[k] != v {
				diff++
			}
		}
		t.Errorf("placement depends on insertion order: %d/500 keys differ", diff)
	}
}

func TestRemovalOnlyMovesRemovedNodesKeys(t *testing.T) {
	r := newRing(t, 64, "cache-a.internal", "cache-b.internal", "cache-c.internal")
	before := mapKeys(t, r, 1000)

	if err := r.RemoveNode("cache-c.internal"); err != nil {
		t.Fatalf("RemoveNode: %v", err)
	}
	after := mapKeys(t, r, 1000)

	moved := 0
	for key, was := range before {
		now := after[key]
		if was == "cache-c.internal" {
			moved++
			if now == "cache-c.internal" {
				t.Errorf("key %q still maps to the removed node", key)
			}
			continue
		}
		if now != was {
			t.Errorf("key %q was on %q (a surviving node) but moved to %q", key, was, now)
		}
	}
	if moved == 0 {
		t.Fatalf("test premise broken: no keys were on the removed node")
	}
}

func TestAdditionOnlyStealsKeysForNewNode(t *testing.T) {
	r := newRing(t, 64, "cache-a.internal", "cache-b.internal")
	before := mapKeys(t, r, 1000)

	if err := r.AddNode("cache-c.internal"); err != nil {
		t.Fatalf("AddNode: %v", err)
	}
	after := mapKeys(t, r, 1000)

	stolen := 0
	for key, was := range before {
		now := after[key]
		if now == was {
			continue
		}
		if now != "cache-c.internal" {
			t.Errorf("key %q moved %q -> %q, but only the new node may steal keys", key, was, now)
		}
		stolen++
	}
	if stolen == 0 {
		t.Errorf("new node received zero keys out of 1000; it is not participating")
	}
}

func TestRemoveThenReAddRestoresPlacement(t *testing.T) {
	r := newRing(t, 64, "cache-a.internal", "cache-b.internal", "cache-c.internal")
	before := mapKeys(t, r, 500)

	if err := r.RemoveNode("cache-b.internal"); err != nil {
		t.Fatalf("RemoveNode: %v", err)
	}
	if err := r.AddNode("cache-b.internal"); err != nil {
		t.Fatalf("AddNode: %v", err)
	}

	if after := mapKeys(t, r, 500); !reflect.DeepEqual(before, after) {
		t.Errorf("removing and re-adding a node did not restore the original placement; placement must be a pure function of membership")
	}
}

func TestVirtualNodesSpreadLoad(t *testing.T) {
	r := newRing(t, 128, "shard-0", "shard-1", "shard-2")
	counts := map[string]int{}
	const total = 3000
	for i := 0; i < total; i++ {
		node, err := r.Get(fmt.Sprintf("tenant-%d/doc-%d", i%37, i))
		if err != nil {
			t.Fatalf("Get: %v", err)
		}
		counts[node]++
	}
	if len(counts) != 3 {
		t.Fatalf("only %d of 3 nodes received keys: %v", len(counts), counts)
	}
	var names []string
	for n := range counts {
		names = append(names, n)
	}
	sort.Strings(names)
	for _, n := range names {
		share := float64(counts[n]) / total
		if share < 0.15 || share > 0.60 {
			t.Errorf("node %s holds %.1f%% of keys (%d/%d); virtual nodes are not balancing the ring", n, share*100, counts[n], total)
		}
	}
}
