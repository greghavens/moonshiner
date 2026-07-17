package metrics

import (
	"sort"
	"strings"
	"sync"
	"testing"
)

func canonical(labels Labels) string {
	keys := make([]string, 0, len(labels))
	for k := range labels {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, k+"="+labels[k])
	}
	return strings.Join(parts, ",")
}

func find(t *testing.T, snap []Metric, name string, labels Labels) Metric {
	t.Helper()
	want := canonical(labels)
	for _, m := range snap {
		if m.Name == name && canonical(m.Labels) == want {
			return m
		}
	}
	t.Fatalf("metric %s{%s} not in snapshot: %v", name, want, snap)
	return Metric{}
}

func TestEmptyRegistrySnapshot(t *testing.T) {
	r := NewRegistry()
	if snap := r.Snapshot(); len(snap) != 0 {
		t.Fatalf("fresh registry snapshot = %v, want empty", snap)
	}
}

func TestCounterIncAndAdd(t *testing.T) {
	r := NewRegistry()
	c := r.Counter("http_requests_total", Labels{"method": "GET"})
	c.Inc()
	c.Inc()
	c.Add(2.5)
	m := find(t, r.Snapshot(), "http_requests_total", Labels{"method": "GET"})
	if m.Value != 4.5 {
		t.Fatalf("counter value = %v, want 4.5", m.Value)
	}
	if m.Kind != KindCounter {
		t.Fatalf("counter kind = %q, want %q", m.Kind, KindCounter)
	}
}

func TestCounterIgnoresNegativeAdd(t *testing.T) {
	r := NewRegistry()
	c := r.Counter("jobs_done_total", nil)
	c.Add(3)
	c.Add(-2)
	m := find(t, r.Snapshot(), "jobs_done_total", nil)
	if m.Value != 3 {
		t.Fatalf("counter after Add(-2) = %v, want 3 (counters are monotonic; negative deltas are dropped)", m.Value)
	}
}

func TestSameNameAndLabelsShareOneSeries(t *testing.T) {
	r := NewRegistry()
	a := r.Counter("cache_hits_total", Labels{"tier": "l1", "node": "a1"})
	b := r.Counter("cache_hits_total", Labels{"node": "a1", "tier": "l1"})
	a.Inc()
	b.Inc()
	snap := r.Snapshot()
	if len(snap) != 1 {
		t.Fatalf("equal name+labels must be ONE series, snapshot has %d: %v", len(snap), snap)
	}
	if snap[0].Value != 2 {
		t.Fatalf("shared series value = %v, want 2", snap[0].Value)
	}
}

func TestDifferentLabelValuesAreDistinctSeries(t *testing.T) {
	r := NewRegistry()
	r.Counter("http_requests_total", Labels{"code": "200"}).Add(7)
	r.Counter("http_requests_total", Labels{"code": "500"}).Inc()
	snap := r.Snapshot()
	if len(snap) != 2 {
		t.Fatalf("want 2 series, got %d: %v", len(snap), snap)
	}
	if m := find(t, snap, "http_requests_total", Labels{"code": "200"}); m.Value != 7 {
		t.Fatalf("code=200 value = %v, want 7", m.Value)
	}
	if m := find(t, snap, "http_requests_total", Labels{"code": "500"}); m.Value != 1 {
		t.Fatalf("code=500 value = %v, want 1", m.Value)
	}
}

func TestRegistryCopiesCallerLabelMap(t *testing.T) {
	r := Labels{"region": "us-east-1"}
	reg := NewRegistry()
	c := reg.Counter("uploads_total", r)
	r["region"] = "eu-west-1" // caller reuses its map; the registry must not care
	c.Inc()
	d := reg.Counter("uploads_total", Labels{"region": "us-east-1"})
	d.Inc()
	snap := reg.Snapshot()
	if len(snap) != 1 {
		t.Fatalf("mutating the caller's label map leaked into the registry: %v", snap)
	}
	m := find(t, snap, "uploads_total", Labels{"region": "us-east-1"})
	if m.Value != 2 {
		t.Fatalf("series value = %v, want 2", m.Value)
	}
}

func TestGaugeSetAndAdd(t *testing.T) {
	r := NewRegistry()
	g := r.Gauge("queue_depth", Labels{"queue": "email"})
	g.Set(10)
	g.Add(-4)
	g.Add(1)
	m := find(t, r.Snapshot(), "queue_depth", Labels{"queue": "email"})
	if m.Value != 7 {
		t.Fatalf("gauge value = %v, want 7 (gauges may go down)", m.Value)
	}
	if m.Kind != KindGauge {
		t.Fatalf("gauge kind = %q, want %q", m.Kind, KindGauge)
	}
	g.Set(3)
	if m := find(t, r.Snapshot(), "queue_depth", Labels{"queue": "email"}); m.Value != 3 {
		t.Fatalf("Set must overwrite, got %v want 3", m.Value)
	}
}

func TestKindConflictPanics(t *testing.T) {
	r := NewRegistry()
	r.Counter("worker_busy", nil)
	defer func() {
		if recover() == nil {
			t.Fatal("registering gauge \"worker_busy\" over an existing counter of the same name must panic")
		}
	}()
	r.Gauge("worker_busy", Labels{"pool": "default"})
}

func TestSnapshotSortedByNameThenLabels(t *testing.T) {
	r := NewRegistry()
	// Registered deliberately out of order.
	r.Counter("http_requests_total", Labels{"code": "500"}).Inc()
	r.Gauge("queue_depth", nil).Set(2)
	r.Counter("http_requests_total", Labels{"code": "200"}).Inc()
	r.Counter("cache_hits_total", nil).Inc()

	snap := r.Snapshot()
	if len(snap) != 4 {
		t.Fatalf("want 4 series, got %d", len(snap))
	}
	got := make([]string, len(snap))
	for i, m := range snap {
		got[i] = m.Name + "{" + canonical(m.Labels) + "}"
	}
	want := []string{
		"cache_hits_total{}",
		"http_requests_total{code=200}",
		"http_requests_total{code=500}",
		"queue_depth{}",
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("snapshot order[%d] = %s, want %s (full: %v)", i, got[i], want[i], got)
		}
	}
}

func TestSnapshotIsIsolatedFromLaterWrites(t *testing.T) {
	r := NewRegistry()
	c := r.Counter("events_total", Labels{"kind": "click"})
	c.Inc()
	snap := r.Snapshot()
	c.Inc()
	c.Inc()
	if m := find(t, snap, "events_total", Labels{"kind": "click"}); m.Value != 1 {
		t.Fatalf("old snapshot changed after later Incs: %v, want 1", m.Value)
	}
	// Mutating a snapshot's label map must not corrupt the registry.
	snap[0].Labels["kind"] = "tampered"
	fresh := r.Snapshot()
	if m := find(t, fresh, "events_total", Labels{"kind": "click"}); m.Value != 3 {
		t.Fatalf("registry corrupted by snapshot mutation: %v", fresh)
	}
}

func TestConcurrentUseIsSafeAndLosesNothing(t *testing.T) {
	r := NewRegistry()
	const workers = 8
	const perWorker = 1000
	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			for i := 0; i < perWorker; i++ {
				// get-or-create races with increments on the same series
				r.Counter("ops_total", Labels{"shard": "s1"}).Inc()
				r.Gauge("last_worker", nil).Set(float64(w))
			}
		}(w)
	}
	// Snapshots taken while writers are running must be safe too.
	for i := 0; i < 50; i++ {
		_ = r.Snapshot()
	}
	wg.Wait()
	m := find(t, r.Snapshot(), "ops_total", Labels{"shard": "s1"})
	if m.Value != float64(workers*perWorker) {
		t.Fatalf("lost increments under concurrency: got %v, want %d", m.Value, workers*perWorker)
	}
	series := 0
	for _, s := range r.Snapshot() {
		if s.Name == "ops_total" {
			series++
		}
	}
	if series != 1 {
		t.Fatalf("concurrent get-or-create produced %d ops_total series, want 1", series)
	}
}
