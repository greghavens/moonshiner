package cronsched

import (
	"fmt"
	"runtime"
	"sync"
	"testing"
	"time"
)

// fakeClock is a hand-cranked clock: the tests move it, nothing sleeps.
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

func firesString(fs []Fire) string {
	out := ""
	for _, f := range fs {
		out += fmt.Sprintf("%s@%s ", f.Name, f.At.Format("15:04"))
	}
	return out
}

func TestAddRemoveValidation(t *testing.T) {
	clk := newFakeClock(at(2026, time.June, 1, 12, 0, 0))
	s := NewScheduler(clk.Now)
	if err := s.Add("backup", "30 3 * * *"); err != nil {
		t.Fatalf("Add of a valid entry: %v", err)
	}
	if err := s.Add("backup", "0 4 * * *"); err == nil {
		t.Fatal("duplicate name must be rejected")
	}
	if err := s.Add("", "* * * * *"); err == nil {
		t.Fatal("empty name must be rejected")
	}
	if err := s.Add("broken", "61 * * * *"); err == nil {
		t.Fatal("invalid expression must be rejected")
	}
	if _, ok := s.NextRun("broken"); ok {
		t.Fatal("a rejected Add must not register the entry")
	}
	if !s.Remove("backup") {
		t.Fatal("Remove of an existing entry must return true")
	}
	if s.Remove("backup") {
		t.Fatal("Remove of a missing entry must return false")
	}
}

func TestPollDeliversEveryMissedFireInOrder(t *testing.T) {
	clk := newFakeClock(at(2026, time.June, 1, 12, 0, 0))
	s := NewScheduler(clk.Now)
	if err := s.Add("beta", "*/15 * * * *"); err != nil {
		t.Fatal(err)
	}
	if err := s.Add("alpha", "*/30 * * * *"); err != nil {
		t.Fatal(err)
	}

	if got := s.Poll(); len(got) != 0 {
		t.Fatalf("nothing is due at add time, got %s", firesString(got))
	}

	clk.Set(at(2026, time.June, 1, 13, 0, 0))
	got := s.Poll()
	want := []Fire{
		{Name: "beta", At: at(2026, time.June, 1, 12, 15, 0)},
		{Name: "alpha", At: at(2026, time.June, 1, 12, 30, 0)},
		{Name: "beta", At: at(2026, time.June, 1, 12, 30, 0)},
		{Name: "beta", At: at(2026, time.June, 1, 12, 45, 0)},
		{Name: "alpha", At: at(2026, time.June, 1, 13, 0, 0)},
		{Name: "beta", At: at(2026, time.June, 1, 13, 0, 0)},
	}
	if len(got) != len(want) {
		t.Fatalf("Poll returned %d fires (%s), want %d (every missed instant, At order, name-tiebreak)", len(got), firesString(got), len(want))
	}
	for i := range want {
		if got[i].Name != want[i].Name || !got[i].At.Equal(want[i].At) {
			t.Fatalf("fire[%d] = %s@%s, want %s@%s", i, got[i].Name, got[i].At, want[i].Name, want[i].At)
		}
	}

	if again := s.Poll(); len(again) != 0 {
		t.Fatalf("second Poll at the same instant re-delivered fires: %s", firesString(again))
	}
}

func TestEntryCursorStartsAtAddTime(t *testing.T) {
	clk := newFakeClock(at(2026, time.June, 2, 12, 7, 30))
	s := NewScheduler(clk.Now)
	if err := s.Add("gamma", "*/15 * * * *"); err != nil {
		t.Fatal(err)
	}
	clk.Set(at(2026, time.June, 2, 12, 20, 0))
	got := s.Poll()
	if len(got) != 1 || got[0].Name != "gamma" || !got[0].At.Equal(at(2026, time.June, 2, 12, 15, 0)) {
		t.Fatalf("entry added 12:07:30, polled 12:20: want exactly gamma@12:15, got %s", firesString(got))
	}
}

func TestClockRewindDeliversNothingAndNeverRedelivers(t *testing.T) {
	clk := newFakeClock(at(2026, time.June, 1, 12, 0, 0))
	s := NewScheduler(clk.Now)
	if err := s.Add("tick", "*/10 * * * *"); err != nil {
		t.Fatal(err)
	}

	clk.Set(at(2026, time.June, 1, 12, 30, 0))
	if got := s.Poll(); len(got) != 3 {
		t.Fatalf("want 3 fires (12:10, 12:20, 12:30), got %s", firesString(got))
	}

	clk.Set(at(2026, time.June, 1, 12, 5, 0)) // ntp yanked the clock backwards
	if got := s.Poll(); len(got) != 0 {
		t.Fatalf("rewound clock must deliver nothing, got %s", firesString(got))
	}

	clk.Set(at(2026, time.June, 1, 12, 30, 0)) // clock catches back up
	if got := s.Poll(); len(got) != 0 {
		t.Fatalf("already-delivered window fired again after a rewind: %s", firesString(got))
	}

	clk.Set(at(2026, time.June, 1, 12, 40, 0))
	got := s.Poll()
	if len(got) != 1 || !got[0].At.Equal(at(2026, time.June, 1, 12, 40, 0)) {
		t.Fatalf("after the rewind recovered, want exactly tick@12:40, got %s", firesString(got))
	}
}

func TestRemoveStopsFiring(t *testing.T) {
	clk := newFakeClock(at(2026, time.June, 1, 9, 0, 0))
	s := NewScheduler(clk.Now)
	if err := s.Add("keep", "0 10 * * *"); err != nil {
		t.Fatal(err)
	}
	if err := s.Add("gone", "30 9 * * *"); err != nil {
		t.Fatal(err)
	}
	if !s.Remove("gone") {
		t.Fatal("Remove(gone) = false, want true")
	}
	clk.Set(at(2026, time.June, 1, 12, 0, 0))
	got := s.Poll()
	if len(got) != 1 || got[0].Name != "keep" || !got[0].At.Equal(at(2026, time.June, 1, 10, 0, 0)) {
		t.Fatalf("removed entry must not fire: want exactly keep@10:00, got %s", firesString(got))
	}
}

func TestNextRunDoesNotConsumeFires(t *testing.T) {
	clk := newFakeClock(at(2026, time.June, 1, 13, 5, 0))
	s := NewScheduler(clk.Now)
	if err := s.Add("beta", "*/15 * * * *"); err != nil {
		t.Fatal(err)
	}

	want := at(2026, time.June, 1, 13, 15, 0)
	for i := 0; i < 2; i++ {
		got, ok := s.NextRun("beta")
		if !ok || !got.Equal(want) {
			t.Fatalf("NextRun #%d = (%s, %v), want (%s, true)", i+1, got, ok, want)
		}
	}
	if got := s.Poll(); len(got) != 0 {
		t.Fatalf("NextRun must not consume or fabricate fires, Poll got %s", firesString(got))
	}

	// With a backlog pending, NextRun still reports the next FUTURE fire...
	clk.Set(at(2026, time.June, 1, 14, 0, 0))
	got, ok := s.NextRun("beta")
	if !ok || !got.Equal(at(2026, time.June, 1, 14, 15, 0)) {
		t.Fatalf("NextRun with backlog = (%s, %v), want (14:15, true)", got, ok)
	}
	// ...and the backlog is still owed to Poll in full.
	if fires := s.Poll(); len(fires) != 4 {
		t.Fatalf("backlog after NextRun: want 4 fires (13:15..14:00), got %s", firesString(fires))
	}

	if _, ok := s.NextRun("nope"); ok {
		t.Fatal("NextRun of an unknown entry must report ok=false")
	}

	if err := s.Add("leapling", "0 0 30 2 *"); err != nil {
		t.Fatal(err)
	}
	if next, ok := s.NextRun("leapling"); !ok || !next.IsZero() {
		t.Fatalf("NextRun of a never-firing entry = (%s, %v), want (zero time, true)", next, ok)
	}
}

func TestConcurrentPollersDeliverEachFireExactlyOnce(t *testing.T) {
	base := at(2026, time.June, 1, 0, 0, 0)
	clk := newFakeClock(base)
	s := NewScheduler(clk.Now)
	if err := s.Add("tick", "* * * * *"); err != nil {
		t.Fatal(err)
	}

	const pollers = 8
	const minutes = 100
	var wg sync.WaitGroup
	collected := make([][]Fire, pollers+1)
	stop := make(chan struct{})
	for p := 0; p < pollers; p++ {
		wg.Add(1)
		go func(p int) {
			defer wg.Done()
			var local []Fire
			for {
				local = append(local, s.Poll()...)
				select {
				case <-stop:
					collected[p] = local
					return
				default:
					runtime.Gosched()
				}
			}
		}(p)
	}

	// Churn the entry table from another goroutine while pollers spin.
	wg.Add(1)
	go func() {
		defer wg.Done()
		for i := 0; i < 50; i++ {
			name := fmt.Sprintf("scratch-%d", i)
			if err := s.Add(name, "* * * * *"); err != nil {
				t.Errorf("concurrent Add(%s): %v", name, err)
				return
			}
			s.NextRun(name)
			s.NextRun("tick")
			if !s.Remove(name) {
				t.Errorf("concurrent Remove(%s) = false, want true", name)
				return
			}
		}
	}()

	for m := 1; m <= minutes; m++ {
		clk.Set(base.Add(time.Duration(m) * time.Minute))
	}
	close(stop)
	wg.Wait()
	collected[pollers] = s.Poll() // sweep anything the pollers missed at the end

	seen := map[string]int{}
	tickTotal := 0
	for _, fires := range collected {
		for _, f := range fires {
			key := f.Name + "@" + f.At.UTC().Format(time.RFC3339)
			seen[key]++
			if seen[key] > 1 {
				t.Fatalf("fire %s was delivered %d times — every fire must go to exactly one Poll caller", key, seen[key])
			}
			if f.Name == "tick" {
				tickTotal++
			}
		}
	}
	if tickTotal != minutes {
		t.Fatalf("tick delivered %d fires across all pollers, want exactly %d", tickTotal, minutes)
	}
	for m := 1; m <= minutes; m++ {
		key := "tick@" + base.Add(time.Duration(m)*time.Minute).UTC().Format(time.RFC3339)
		if seen[key] != 1 {
			t.Fatalf("fire %s was delivered %d times, want exactly once", key, seen[key])
		}
	}
}
