package sortid

import (
	"sort"
	"sync"
	"testing"
	"time"
)

// Acceptance contract for the new time-sortable IDs: NewSortable with an
// injectable clock, Sortable.Next, and Parse.

type fakeClock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *fakeClock) advance(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

// 2023-11-14T22:13:20Z, chosen so the hex encoding is easy to eyeball.
var epoch = time.UnixMilli(1700000000000).UTC()

func TestNextEncodesMillisAndSequenceAsLowercaseHex(t *testing.T) {
	clk := &fakeClock{t: epoch}
	s := NewSortable(clk.Now)
	if got, want := s.Next(), "018bcfe568000000"; got != want {
		t.Fatalf("first Next() = %q, want %q", got, want)
	}
	if got, want := s.Next(), "018bcfe568000001"; got != want {
		t.Fatalf("second Next() in same millisecond = %q, want %q", got, want)
	}
}

func TestSameMillisecondIDsAreStrictlyIncreasing(t *testing.T) {
	clk := &fakeClock{t: epoch}
	s := NewSortable(clk.Now)
	prev := s.Next()
	for i := 0; i < 20; i++ {
		id := s.Next()
		if !(id > prev) {
			t.Fatalf("id %d %q does not sort after %q", i+2, id, prev)
		}
		p, err := Parse(id)
		if err != nil {
			t.Fatalf("Parse(%q): %v", id, err)
		}
		if !p.Time.Equal(epoch) {
			t.Fatalf("Parse(%q).Time = %v, want %v", id, p.Time, epoch)
		}
		if p.Seq != uint16(i+1) {
			t.Fatalf("Parse(%q).Seq = %d, want %d", id, p.Seq, i+1)
		}
		prev = id
	}
}

func TestIDsSortLexicographicallyAcrossMilliseconds(t *testing.T) {
	clk := &fakeClock{t: epoch}
	s := NewSortable(clk.Now)
	var ids []string
	for i := 0; i < 50; i++ {
		ids = append(ids, s.Next())
		ids = append(ids, s.Next())
		clk.advance(3 * time.Millisecond)
	}
	if !sort.StringsAreSorted(ids) {
		t.Fatal("IDs generated in time order are not lexicographically sorted")
	}
	for i := 1; i < len(ids); i++ {
		if ids[i] == ids[i-1] {
			t.Fatalf("duplicate ID %q", ids[i])
		}
	}
}

func TestClockGoingBackwardsNeverBreaksOrdering(t *testing.T) {
	clk := &fakeClock{t: epoch}
	s := NewSortable(clk.Now)
	s.Next()
	clk.advance(5 * time.Millisecond)
	high := s.Next()
	clk.advance(-10 * time.Millisecond) // NTP step: wall clock jumps back
	rewound := s.Next()
	if !(rewound > high) {
		t.Fatalf("ID issued after clock rewind %q sorts before %q", rewound, high)
	}
	p, err := Parse(rewound)
	if err != nil {
		t.Fatalf("Parse(%q): %v", rewound, err)
	}
	if want := epoch.Add(5 * time.Millisecond); !p.Time.Equal(want) {
		t.Fatalf("rewound ID timestamp = %v, want last-used millisecond %v", p.Time, want)
	}
	if p.Seq != 1 {
		t.Fatalf("rewound ID Seq = %d, want 1", p.Seq)
	}
}

func TestSequenceOverflowSpillsIntoNextMillisecond(t *testing.T) {
	clk := &fakeClock{t: epoch}
	s := NewSortable(clk.Now)
	var last string
	for i := 0; i < 65536; i++ { // exhausts seq 0000..ffff at one millisecond
		last = s.Next()
	}
	if want := "018bcfe56800ffff"; last != want {
		t.Fatalf("65536th ID = %q, want %q", last, want)
	}
	spill := s.Next()
	if want := "018bcfe568010000"; spill != want {
		t.Fatalf("overflow ID = %q, want %q", spill, want)
	}
	p, err := Parse(spill)
	if err != nil {
		t.Fatalf("Parse(%q): %v", spill, err)
	}
	if want := epoch.Add(time.Millisecond); !p.Time.Equal(want) || p.Seq != 0 {
		t.Fatalf("overflow ID parsed as (%v, %d), want (%v, 0)", p.Time, p.Seq, want)
	}
}

func TestParseRoundTripReturnsUTCMillisecond(t *testing.T) {
	at := time.Date(2026, 7, 11, 9, 30, 0, 250e6, time.FixedZone("PST", -8*3600))
	clk := &fakeClock{t: at}
	s := NewSortable(clk.Now)
	p, err := Parse(s.Next())
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if p.Time.Location() != time.UTC {
		t.Fatalf("Parse Time location = %v, want UTC", p.Time.Location())
	}
	if !p.Time.Equal(at.UTC().Truncate(time.Millisecond)) {
		t.Fatalf("Parse Time = %v, want %v", p.Time, at.UTC().Truncate(time.Millisecond))
	}
	zero, err := Parse("0000000000000000")
	if err != nil {
		t.Fatalf("Parse(all zeros): %v", err)
	}
	if !zero.Time.Equal(time.UnixMilli(0)) || zero.Seq != 0 {
		t.Fatalf("Parse(all zeros) = (%v, %d), want (unix epoch, 0)", zero.Time, zero.Seq)
	}
}

func TestParseRejectsMalformedIDs(t *testing.T) {
	bad := []string{
		"",                     // empty
		"018bcfe5680000",       // too short
		"018bcfe56800000000",   // too long
		"018bcfe56800zzzz",     // non-hex characters
		"018BCFE568000000",     // uppercase is not canonical
		"018bcfe56800 000",     // embedded space
		"job-000001",           // counter-style ID
	}
	for _, id := range bad {
		if _, err := Parse(id); err == nil {
			t.Fatalf("Parse(%q) succeeded, want error", id)
		}
	}
}

func TestSortableIsSafeForConcurrentUse(t *testing.T) {
	clk := &fakeClock{t: epoch}
	s := NewSortable(clk.Now)
	const workers, per = 8, 100
	var mu sync.Mutex
	seen := make(map[string]bool)
	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < per; i++ {
				id := s.Next()
				mu.Lock()
				seen[id] = true
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	if len(seen) != workers*per {
		t.Fatalf("got %d distinct IDs from %d calls", len(seen), workers*per)
	}
}
