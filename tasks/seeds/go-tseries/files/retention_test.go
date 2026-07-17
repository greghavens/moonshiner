package tseries

import (
	"fmt"
	"sync"
	"testing"
	"time"
)

func TestExpiredSamplesInvisibleBeforeSweep(t *testing.T) {
	clk := newFakeClock(at(12, 0, 0))
	st := NewStore(10*time.Minute, clk.Now)
	mustAppend(t, st, "cpu", at(11, 55, 0), 1)
	mustAppend(t, st, "cpu", at(11, 58, 0), 2)

	clk.Set(at(12, 6, 0)) // cutoff 11:56 — the 11:55 point just expired
	got := st.Query("cpu", at(11, 0, 0), at(12, 0, 0))
	if len(got) != 1 || got[0].V != 2 {
		t.Fatalf("Query returned %+v, want only the 11:58 sample: expired points must not surface even before any Sweep", got)
	}
	if n := st.Len("cpu"); n != 1 {
		t.Fatalf("Len = %d, want 1 (live samples only)", n)
	}

	ds, err := st.Downsample("cpu", at(11, 0, 0), at(12, 0, 0), time.Hour)
	if err != nil {
		t.Fatalf("Downsample: %v", err)
	}
	if len(ds) != 1 || ds[0].Count != 1 || ds[0].Min != 2 {
		t.Fatalf("Downsample = %+v, want a single bucket built from the one live sample", ds)
	}
}

func TestRetentionBoundaryIsInclusive(t *testing.T) {
	clk := newFakeClock(at(12, 0, 0))
	st := NewStore(10*time.Minute, clk.Now)
	mustAppend(t, st, "cpu", at(12, 0, 0), 1)

	clk.Set(at(12, 10, 0)) // cutoff is exactly the sample's timestamp
	if got := st.Query("cpu", at(11, 0, 0), at(13, 0, 0)); len(got) != 0 {
		t.Fatalf("sample exactly retention old must be expired (t <= now-retention), got %+v", got)
	}

	mustAppend(t, st, "cpu", at(12, 0, 1), 2) // one second inside the window
	clk.Set(at(12, 10, 0))
	if got := st.Query("cpu", at(11, 0, 0), at(13, 0, 0)); len(got) != 1 || got[0].V != 2 {
		t.Fatalf("sample one second younger than the cutoff must be live, got %+v", got)
	}
}

func TestAppendAlreadyExpiredIsRejected(t *testing.T) {
	clk := newFakeClock(at(12, 0, 0))
	st := NewStore(10*time.Minute, clk.Now)
	if err := st.Append("cpu", at(11, 49, 0), 1); err == nil {
		t.Fatal("Append of a sample already older than the retention window must return an error")
	}
	if n := st.Len("cpu"); n != 0 {
		t.Fatalf("rejected append must not be stored, Len = %d", n)
	}
}

func TestSweepEvictsAndReportsCount(t *testing.T) {
	clk := newFakeClock(at(12, 0, 0))
	st := NewStore(10*time.Minute, clk.Now)
	mustAppend(t, st, "cpu", at(11, 52, 0), 1)
	mustAppend(t, st, "cpu", at(11, 54, 0), 2)
	mustAppend(t, st, "cpu", at(11, 59, 0), 3)
	mustAppend(t, st, "mem", at(11, 53, 0), 4)
	mustAppend(t, st, "mem", at(11, 58, 0), 5)

	clk.Set(at(12, 5, 0)) // cutoff 11:55 — expires 11:52, 11:54, 11:53
	if n := st.Sweep(); n != 3 {
		t.Fatalf("Sweep evicted %d samples, want 3", n)
	}
	if n := st.Sweep(); n != 0 {
		t.Fatalf("second Sweep evicted %d samples, want 0", n)
	}
	if n := st.Len("cpu"); n != 1 {
		t.Fatalf("cpu Len after sweep = %d, want 1", n)
	}
	if n := st.Len("mem"); n != 1 {
		t.Fatalf("mem Len after sweep = %d, want 1", n)
	}
}

func TestSeriesDisappearsWhenAllSamplesExpire(t *testing.T) {
	clk := newFakeClock(at(12, 0, 0))
	st := NewStore(10*time.Minute, clk.Now)
	mustAppend(t, st, "old", at(11, 52, 0), 1)
	mustAppend(t, st, "fresh", at(11, 59, 0), 2)

	clk.Set(at(12, 5, 0))
	got := st.Series()
	if len(got) != 1 || got[0] != "fresh" {
		t.Fatalf("Series() = %v, want [fresh]: a series whose samples all expired must not be listed", got)
	}
}

func TestZeroRetentionKeepsForever(t *testing.T) {
	clk := newFakeClock(at(12, 0, 0))
	st := NewStore(0, clk.Now)
	mustAppend(t, st, "cpu", at(1, 0, 0), 1)
	clk.Set(at(23, 0, 0))
	if n := st.Sweep(); n != 0 {
		t.Fatalf("Sweep with retention 0 evicted %d samples, want 0 (keep forever)", n)
	}
	if got := st.Query("cpu", at(0, 0, 0), at(23, 0, 0)); len(got) != 1 {
		t.Fatalf("retention 0 must keep everything, Query returned %d samples", len(got))
	}
}

func TestConcurrentAppendQuerySweep(t *testing.T) {
	clk := newFakeClock(at(12, 0, 0))
	st := NewStore(30*time.Minute, clk.Now)

	const writers = 8
	const perWriter = 200
	var wg sync.WaitGroup
	for w := 0; w < writers; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			series := fmt.Sprintf("s%d", w%4) // writers share series pairwise
			for i := 0; i < perWriter; i++ {
				ts := at(12, 0, 0).Add(time.Duration(w*perWriter+i) * time.Millisecond)
				if err := st.Append(series, ts, float64(i)); err != nil {
					t.Errorf("concurrent Append: %v", err)
					return
				}
			}
		}(w)
	}
	// Readers and sweepers churn while writers run.
	for r := 0; r < 4; r++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < 50; i++ {
				st.Query("s0", at(11, 0, 0), at(13, 0, 0))
				st.Series()
				st.Sweep()
				if _, err := st.Downsample("s1", at(12, 0, 0), at(12, 1, 0), time.Second); err != nil {
					t.Errorf("concurrent Downsample: %v", err)
					return
				}
			}
		}()
	}
	wg.Wait()

	total := 0
	for _, s := range st.Series() {
		total += st.Len(s)
	}
	if total != writers*perWriter {
		t.Fatalf("after concurrent writes store holds %d samples, want %d (all timestamps were distinct)", total, writers*perWriter)
	}
}
