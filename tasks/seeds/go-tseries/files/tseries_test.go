package tseries

import (
	"math"
	"sync"
	"testing"
	"time"
)

// fakeClock is hand-cranked: tests move it, nothing sleeps.
type fakeClock struct {
	mu sync.Mutex
	t  time.Time
}

func newFakeClock(start time.Time) *fakeClock { return &fakeClock{t: start} }

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *fakeClock) Set(t time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.t = t
}

func at(h, m, s int) time.Time {
	return time.Date(2026, time.March, 5, h, m, s, 0, time.UTC)
}

func mustAppend(t *testing.T, st *Store, series string, ts time.Time, v float64) {
	t.Helper()
	if err := st.Append(series, ts, v); err != nil {
		t.Fatalf("Append(%q, %s, %v): %v", series, ts, v, err)
	}
}

func sampleTimes(ss []Sample) []time.Time {
	out := make([]time.Time, len(ss))
	for i, s := range ss {
		out[i] = s.T
	}
	return out
}

func TestQueryReturnsHalfOpenSortedRange(t *testing.T) {
	clk := newFakeClock(at(12, 0, 0))
	st := NewStore(0, clk.Now)

	// Deliberately appended out of order — agents flush late.
	mustAppend(t, st, "cpu", at(10, 3, 0), 3)
	mustAppend(t, st, "cpu", at(10, 1, 0), 1)
	mustAppend(t, st, "cpu", at(10, 5, 0), 5)
	mustAppend(t, st, "cpu", at(10, 2, 0), 2)
	mustAppend(t, st, "cpu", at(10, 4, 0), 4)

	got := st.Query("cpu", at(10, 2, 0), at(10, 5, 0))
	if len(got) != 3 {
		t.Fatalf("Query [10:02,10:05) returned %d samples (%v), want 3: from is inclusive, to is exclusive", len(got), sampleTimes(got))
	}
	for i, wantV := range []float64{2, 3, 4} {
		if got[i].V != wantV {
			t.Fatalf("sample[%d].V = %v, want %v (ascending time order)", i, got[i].V, wantV)
		}
	}
	for i := 1; i < len(got); i++ {
		if !got[i-1].T.Before(got[i].T) {
			t.Fatalf("samples not strictly ascending: %v", sampleTimes(got))
		}
	}
}

func TestQueryEmptyCases(t *testing.T) {
	clk := newFakeClock(at(12, 0, 0))
	st := NewStore(0, clk.Now)
	mustAppend(t, st, "cpu", at(10, 0, 0), 1)

	if got := st.Query("mem", at(9, 0, 0), at(11, 0, 0)); len(got) != 0 {
		t.Fatalf("Query on unknown series returned %d samples, want 0", len(got))
	}
	if got := st.Query("cpu", at(10, 0, 0), at(10, 0, 0)); len(got) != 0 {
		t.Fatalf("Query with from == to returned %d samples, want 0 (half-open window is empty)", len(got))
	}
	if got := st.Query("cpu", at(11, 0, 0), at(9, 0, 0)); len(got) != 0 {
		t.Fatalf("Query with from after to returned %d samples, want 0", len(got))
	}
}

func TestAppendSameTimestampOverwrites(t *testing.T) {
	clk := newFakeClock(at(12, 0, 0))
	st := NewStore(0, clk.Now)
	mustAppend(t, st, "cpu", at(10, 0, 0), 1)
	mustAppend(t, st, "cpu", at(10, 1, 0), 2)
	mustAppend(t, st, "cpu", at(10, 0, 0), 9) // corrected reading, same instant

	if n := st.Len("cpu"); n != 2 {
		t.Fatalf("Len after duplicate-timestamp append = %d, want 2 (upsert, not a new point)", n)
	}
	got := st.Query("cpu", at(9, 0, 0), at(11, 0, 0))
	if len(got) != 2 || got[0].V != 9 || got[1].V != 2 {
		t.Fatalf("after overwrite Query = %+v, want values [9 2] in time order", got)
	}
}

func TestAppendRejectsEmptySeriesName(t *testing.T) {
	st := NewStore(0, nil)
	if err := st.Append("", at(10, 0, 0), 1); err == nil {
		t.Fatal("Append with empty series name must return an error")
	}
	if n := len(st.Series()); n != 0 {
		t.Fatalf("failed Append must not create a series, Series() has %d entries", n)
	}
}

func TestSeriesSortedAndLive(t *testing.T) {
	clk := newFakeClock(at(12, 0, 0))
	st := NewStore(0, clk.Now)
	mustAppend(t, st, "zeta", at(10, 0, 0), 1)
	mustAppend(t, st, "alpha", at(10, 0, 0), 1)
	mustAppend(t, st, "mid", at(10, 0, 0), 1)

	got := st.Series()
	want := []string{"alpha", "mid", "zeta"}
	if len(got) != len(want) {
		t.Fatalf("Series() = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("Series() = %v, want %v (sorted ascending)", got, want)
		}
	}
}

func TestDownsampleBucketsAlignedToFrom(t *testing.T) {
	clk := newFakeClock(at(13, 0, 0))
	st := NewStore(0, clk.Now)
	mustAppend(t, st, "cpu", at(12, 0, 0), 1)
	mustAppend(t, st, "cpu", at(12, 0, 30), 3)
	mustAppend(t, st, "cpu", at(12, 1, 10), 10)
	mustAppend(t, st, "cpu", at(12, 3, 50), -2)

	got, err := st.Downsample("cpu", at(12, 0, 0), at(12, 4, 0), time.Minute)
	if err != nil {
		t.Fatalf("Downsample: %v", err)
	}
	want := []Bucket{
		{Start: at(12, 0, 0), Min: 1, Max: 3, Avg: 2, Count: 2},
		{Start: at(12, 1, 0), Min: 10, Max: 10, Avg: 10, Count: 1},
		{Start: at(12, 3, 0), Min: -2, Max: -2, Avg: -2, Count: 1},
	}
	if len(got) != len(want) {
		t.Fatalf("Downsample returned %d buckets (%+v), want %d — empty buckets must be omitted", len(got), got, len(want))
	}
	for i := range want {
		g, w := got[i], want[i]
		if !g.Start.Equal(w.Start) || g.Min != w.Min || g.Max != w.Max || g.Count != w.Count || math.Abs(g.Avg-w.Avg) > 1e-9 {
			t.Fatalf("bucket[%d] = %+v, want %+v", i, g, w)
		}
	}
}

func TestDownsampleAlignmentFollowsFromNotEpoch(t *testing.T) {
	clk := newFakeClock(at(13, 0, 0))
	st := NewStore(0, clk.Now)
	mustAppend(t, st, "cpu", at(12, 0, 45), 4)
	mustAppend(t, st, "cpu", at(12, 1, 15), 8)

	// Window starts at 12:00:30, so the first bucket is [12:00:30, 12:01:30)
	// and both samples land in it together.
	got, err := st.Downsample("cpu", at(12, 0, 30), at(12, 2, 30), time.Minute)
	if err != nil {
		t.Fatalf("Downsample: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("got %d buckets (%+v), want 1: buckets align to from, not to the wall clock", len(got), got)
	}
	b := got[0]
	if !b.Start.Equal(at(12, 0, 30)) || b.Count != 2 || b.Min != 4 || b.Max != 8 || math.Abs(b.Avg-6) > 1e-9 {
		t.Fatalf("bucket = %+v, want Start=12:00:30 Count=2 Min=4 Max=8 Avg=6", b)
	}
}

func TestDownsampleWindowEdges(t *testing.T) {
	clk := newFakeClock(at(13, 0, 0))
	st := NewStore(0, clk.Now)
	mustAppend(t, st, "cpu", at(12, 0, 0), 1)  // exactly at from — included
	mustAppend(t, st, "cpu", at(12, 2, 0), 99) // exactly at to — excluded
	mustAppend(t, st, "cpu", at(12, 1, 59), 5)

	got, err := st.Downsample("cpu", at(12, 0, 0), at(12, 2, 0), time.Minute)
	if err != nil {
		t.Fatalf("Downsample: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("got %d buckets (%+v), want 2", len(got), got)
	}
	if got[0].Count != 1 || got[0].Min != 1 {
		t.Fatalf("first bucket = %+v, want the sample at from included", got[0])
	}
	if got[1].Count != 1 || got[1].Max != 5 {
		t.Fatalf("second bucket = %+v, want only 12:01:59 (sample at to excluded)", got[1])
	}
}

func TestDownsampleRejectsBadBucket(t *testing.T) {
	st := NewStore(0, nil)
	if _, err := st.Downsample("cpu", at(12, 0, 0), at(13, 0, 0), 0); err == nil {
		t.Fatal("Downsample with zero bucket must return an error")
	}
	if _, err := st.Downsample("cpu", at(12, 0, 0), at(13, 0, 0), -time.Minute); err == nil {
		t.Fatal("Downsample with negative bucket must return an error")
	}
}
