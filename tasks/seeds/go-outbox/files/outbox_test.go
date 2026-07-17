package outbox

import (
	"errors"
	"fmt"
	"reflect"
	"sync"
	"testing"
	"time"
)

var t0 = time.Date(2026, 5, 1, 9, 0, 0, 0, time.UTC)

// fakeClock is an injectable clock the tests advance by hand.
type fakeClock struct {
	mu sync.Mutex
	t  time.Time
}

func newClock() *fakeClock { return &fakeClock{t: t0} }

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *fakeClock) Advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.t = c.t.Add(d)
}

// scriptSink returns scripted errors per delivery id: the i-th delivery of a
// given id pops the i-th entry of its script (nil-padded: past the end of the
// script everything succeeds). Every call is logged.
type scriptSink struct {
	mu     sync.Mutex
	script map[string][]error
	calls  []string // record ids, in call order
	byKey  map[string][]string
}

func newSink(script map[string][]error) *scriptSink {
	return &scriptSink{script: script, byKey: map[string][]string{}}
}

func (s *scriptSink) Deliver(r Record) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.calls = append(s.calls, r.ID)
	s.byKey[r.Key] = append(s.byKey[r.Key], r.ID)
	if q := s.script[r.ID]; len(q) > 0 {
		err := q[0]
		s.script[r.ID] = q[1:]
		return err
	}
	return nil
}

func (s *scriptSink) callsFor(id string) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	n := 0
	for _, c := range s.calls {
		if c == id {
			n++
		}
	}
	return n
}

func find(t *testing.T, d *Dispatcher, id string) Record {
	t.Helper()
	for _, r := range d.Snapshot() {
		if r.ID == id {
			return r
		}
	}
	t.Fatalf("record %q not found in snapshot", id)
	return Record{}
}

func mustAdd(t *testing.T, d *Dispatcher, id, key, payload string) {
	t.Helper()
	if err := d.Add(id, key, payload); err != nil {
		t.Fatalf("Add(%s): %v", id, err)
	}
}

func TestDeliversInSeqOrderAcrossKeys(t *testing.T) {
	clk := newClock()
	sink := newSink(nil)
	d := New(sink, Options{Now: clk.Now})
	mustAdd(t, d, "r1", "order-1", "created")
	mustAdd(t, d, "r2", "order-2", "created")
	mustAdd(t, d, "r3", "order-1", "paid")

	res := d.Dispatch()
	if want := []string{"r1", "r2", "r3"}; !reflect.DeepEqual(res.Delivered, want) {
		t.Fatalf("Delivered = %v, want %v", res.Delivered, want)
	}
	if len(res.Retried) != 0 || len(res.Parked) != 0 {
		t.Fatalf("unexpected retried/parked: %v / %v", res.Retried, res.Parked)
	}
	if res.Retried == nil || res.Parked == nil {
		t.Fatal("Result slices must be non-nil even when empty")
	}
	for _, id := range []string{"r1", "r2", "r3"} {
		if r := find(t, d, id); r.State != "delivered" {
			t.Fatalf("%s state = %q, want delivered", id, r.State)
		}
	}
	// nothing left: an idle pass reports empty non-nil slices
	res = d.Dispatch()
	if res.Delivered == nil || len(res.Delivered) != 0 {
		t.Fatalf("idle Dispatch Delivered = %#v", res.Delivered)
	}
}

func TestAddValidation(t *testing.T) {
	d := New(newSink(nil), Options{Now: newClock().Now})
	mustAdd(t, d, "r1", "k", "p")
	if err := d.Add("r1", "k", "p"); err == nil {
		t.Fatal("duplicate id must be rejected")
	}
	if err := d.Add("", "k", "p"); err == nil {
		t.Fatal("empty id must be rejected")
	}
	if err := d.Add("r2", "", "p"); err == nil {
		t.Fatal("empty key must be rejected")
	}
	if r := find(t, d, "r1"); r.Seq != 1 || r.State != "pending" || r.Attempts != 0 {
		t.Fatalf("fresh record shape wrong: %+v", r)
	}
}

func TestFailureBlocksOnlyItsOwnKey(t *testing.T) {
	clk := newClock()
	sink := newSink(map[string][]error{
		"a1": {errors.New("connection reset")},
	})
	d := New(sink, Options{Now: clk.Now})
	mustAdd(t, d, "a1", "acct-9", "sync")
	mustAdd(t, d, "a2", "acct-9", "sync-again")
	mustAdd(t, d, "b1", "acct-4", "sync")

	res := d.Dispatch()
	if want := []string{"b1"}; !reflect.DeepEqual(res.Delivered, want) {
		t.Fatalf("Delivered = %v, want %v", res.Delivered, want)
	}
	if want := []string{"a1"}; !reflect.DeepEqual(res.Retried, want) {
		t.Fatalf("Retried = %v, want %v", res.Retried, want)
	}
	// a2 must not have been attempted at all: a1 still blocks acct-9
	if n := sink.callsFor("a2"); n != 0 {
		t.Fatalf("a2 was attempted %d times while a1 was pending", n)
	}
	if r := find(t, d, "a2"); r.State != "pending" {
		t.Fatalf("a2 state = %q, want pending", r.State)
	}

	// a1 is backing off: even a due a2 stays blocked behind it
	res = d.Dispatch()
	if len(res.Delivered)+len(res.Retried)+len(res.Parked) != 0 {
		t.Fatalf("nothing should be due yet: %+v", res)
	}

	clk.Advance(time.Second) // a1's first backoff expires
	res = d.Dispatch()
	if want := []string{"a1", "a2"}; !reflect.DeepEqual(res.Delivered, want) {
		t.Fatalf("Delivered = %v, want %v (key unblocks in the same pass)", res.Delivered, want)
	}
	if got := sink.byKey["acct-9"]; !reflect.DeepEqual(got, []string{"a1", "a1", "a2"}) {
		t.Fatalf("per-key call order = %v", got)
	}
}

func TestExponentialBackoffScheduleWithCap(t *testing.T) {
	clk := newClock()
	sink := newSink(map[string][]error{
		"j1": {
			errors.New("e1"), errors.New("e2"), errors.New("e3"),
			errors.New("e4"), errors.New("e5"),
		},
	})
	d := New(sink, Options{
		Now:         clk.Now,
		MaxAttempts: 10,
		BaseDelay:   time.Second,
		MaxDelay:    5 * time.Second,
	})
	mustAdd(t, d, "j1", "job", "run")

	// failures schedule 1s, 2s, 4s, then cap at 5s
	wantDelays := []time.Duration{
		time.Second, 2 * time.Second, 4 * time.Second, 5 * time.Second, 5 * time.Second,
	}
	for i, want := range wantDelays {
		res := d.Dispatch()
		if !reflect.DeepEqual(res.Retried, []string{"j1"}) {
			t.Fatalf("attempt %d: Retried = %v", i+1, res.Retried)
		}
		r := find(t, d, "j1")
		if r.Attempts != i+1 {
			t.Fatalf("attempt %d: Attempts = %d", i+1, r.Attempts)
		}
		if got := r.NextAttemptAt.Sub(clk.Now()); got != want {
			t.Fatalf("attempt %d: backoff = %v, want %v", i+1, got, want)
		}
		// one second short of the schedule: still not due
		clk.Advance(want - time.Second)
		if res := d.Dispatch(); len(res.Retried)+len(res.Delivered) != 0 {
			t.Fatalf("attempt %d: dispatched early: %+v", i+1, res)
		}
		clk.Advance(time.Second)
	}

	// script exhausted: the sixth attempt succeeds (and counts as an attempt)
	res := d.Dispatch()
	if !reflect.DeepEqual(res.Delivered, []string{"j1"}) {
		t.Fatalf("final Delivered = %v", res.Delivered)
	}
	if r := find(t, d, "j1"); r.Attempts != 6 || r.State != "delivered" {
		t.Fatalf("final record: %+v", r)
	}
}

func TestParkingAfterMaxAttemptsUnblocksKey(t *testing.T) {
	clk := newClock()
	sink := newSink(map[string][]error{
		"p1": {errors.New("schema mismatch"), errors.New("schema mismatch")},
	})
	d := New(sink, Options{Now: clk.Now, MaxAttempts: 2, BaseDelay: time.Second})
	mustAdd(t, d, "p1", "acct-7", "v1")
	mustAdd(t, d, "p2", "acct-7", "v2")

	res := d.Dispatch()
	if !reflect.DeepEqual(res.Retried, []string{"p1"}) {
		t.Fatalf("first pass Retried = %v", res.Retried)
	}
	clk.Advance(time.Second)
	res = d.Dispatch()
	if !reflect.DeepEqual(res.Parked, []string{"p1"}) {
		t.Fatalf("Parked = %v", res.Parked)
	}
	// parking unblocked acct-7 within the same pass
	if !reflect.DeepEqual(res.Delivered, []string{"p2"}) {
		t.Fatalf("Delivered = %v (p2 should go out once p1 parks)", res.Delivered)
	}
	r := find(t, d, "p1")
	if r.State != "parked" || r.Reason != "schema mismatch" || r.Attempts != 2 {
		t.Fatalf("parked record: %+v", r)
	}
}

func TestParkedRecordCanBeResubmitted(t *testing.T) {
	clk := newClock()
	sink := newSink(map[string][]error{"p1": {errors.New("boom")}})
	d := New(sink, Options{Now: clk.Now, MaxAttempts: 1})
	mustAdd(t, d, "p1", "k", "payload")

	res := d.Dispatch()
	if !reflect.DeepEqual(res.Parked, []string{"p1"}) {
		t.Fatalf("Parked = %v", res.Parked)
	}
	if err := d.Retry("p1"); err != nil {
		t.Fatalf("Retry: %v", err)
	}
	r := find(t, d, "p1")
	if r.State != "pending" || r.Attempts != 0 || r.Reason != "" {
		t.Fatalf("resubmitted record: %+v", r)
	}
	res = d.Dispatch()
	if !reflect.DeepEqual(res.Delivered, []string{"p1"}) {
		t.Fatalf("after Retry, Delivered = %v", res.Delivered)
	}

	if err := d.Retry("p1"); err == nil {
		t.Fatal("Retry on a delivered record must fail")
	}
	if err := d.Retry("ghost"); err == nil {
		t.Fatal("Retry on an unknown id must fail")
	}
}

func TestAtLeastOnceOnAmbiguousSinkError(t *testing.T) {
	clk := newClock()
	// the sink got the payload out but reported a timeout: the dispatcher must
	// NOT mark the record delivered, so the receiver sees it twice
	sink := newSink(map[string][]error{"x1": {errors.New("deadline exceeded")}})
	d := New(sink, Options{Now: clk.Now})
	mustAdd(t, d, "x1", "k", "charge")

	d.Dispatch()
	if r := find(t, d, "x1"); r.State != "pending" {
		t.Fatalf("after ambiguous error, state = %q, want pending", r.State)
	}
	clk.Advance(time.Second)
	res := d.Dispatch()
	if !reflect.DeepEqual(res.Delivered, []string{"x1"}) {
		t.Fatalf("Delivered = %v", res.Delivered)
	}
	if n := sink.callsFor("x1"); n != 2 {
		t.Fatalf("sink saw x1 %d times, want 2 (at-least-once)", n)
	}
	if r := find(t, d, "x1"); r.Attempts != 2 || r.State != "delivered" {
		t.Fatalf("final record: %+v", r)
	}
}

func TestSnapshotIsACopy(t *testing.T) {
	d := New(newSink(nil), Options{Now: newClock().Now})
	mustAdd(t, d, "r1", "k", "p")
	snap := d.Snapshot()
	snap[0].State = "parked"
	snap[0].Payload = "scribbled"
	if r := find(t, d, "r1"); r.State != "pending" || r.Payload != "p" {
		t.Fatalf("mutating a snapshot leaked into the store: %+v", r)
	}
}

func TestLoadRestartRedeliversOnlyPending(t *testing.T) {
	clk := newClock()
	sink := newSink(map[string][]error{
		"b1": {errors.New("down")},
		"c1": {errors.New("bad"), errors.New("bad")},
	})
	d := New(sink, Options{Now: clk.Now, MaxAttempts: 2, BaseDelay: time.Second})
	mustAdd(t, d, "a1", "ka", "done-before-crash")
	mustAdd(t, d, "b1", "kb", "in-flight")
	mustAdd(t, d, "c1", "kc", "poison")
	d.Dispatch() // a1 delivered; b1 and c1 each fail once
	clk.Advance(time.Second)
	d.Dispatch() // b1's script is spent, so it delivers; c1 fails again and parks

	// simulate a crash where b1's delivered mark never hit disk: reload the
	// snapshot with b1 rewound to pending
	records := d.Snapshot()
	for i := range records {
		if records[i].ID == "b1" {
			records[i].State = "pending"
			records[i].NextAttemptAt = clk.Now()
		}
	}
	sink2 := newSink(nil)
	d2, err := Load(records, sink2, Options{Now: clk.Now, MaxAttempts: 2})
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	res := d2.Dispatch()
	if !reflect.DeepEqual(res.Delivered, []string{"b1"}) {
		t.Fatalf("after restart Delivered = %v, want only b1", res.Delivered)
	}
	if n := sink2.callsFor("a1"); n != 0 {
		t.Fatal("a delivered record must not be re-sent after restart")
	}
	if r := find(t, d2, "c1"); r.State != "parked" || r.Reason != "bad" {
		t.Fatalf("parked record must survive restart: %+v", r)
	}
	// Seq reassigned in slice order
	for i, r := range d2.Snapshot() {
		if r.Seq != i+1 {
			t.Fatalf("Seq[%d] = %d after Load", i, r.Seq)
		}
	}
}

func TestLoadValidation(t *testing.T) {
	opts := Options{Now: newClock().Now}
	if _, err := Load([]Record{
		{ID: "x", Key: "k", State: "pending"},
		{ID: "x", Key: "k", State: "pending"},
	}, newSink(nil), opts); err == nil {
		t.Fatal("duplicate ids in Load must error")
	}
	if _, err := Load([]Record{
		{ID: "x", Key: "k", State: "limbo"},
	}, newSink(nil), opts); err == nil {
		t.Fatal("unknown state in Load must error")
	}
}

func TestConcurrentAddAndDispatch(t *testing.T) {
	clk := newClock()
	sink := newSink(nil)
	d := New(sink, Options{Now: clk.Now})

	const producers, perProducer = 4, 25
	var wg sync.WaitGroup
	for p := 0; p < producers; p++ {
		wg.Add(1)
		go func(p int) {
			defer wg.Done()
			key := fmt.Sprintf("key-%d", p)
			for i := 0; i < perProducer; i++ {
				if err := d.Add(fmt.Sprintf("r-%d-%d", p, i), key, "payload"); err != nil {
					t.Errorf("Add: %v", err)
				}
				if i%5 == 0 {
					d.Dispatch()
				}
			}
		}(p)
	}
	wg.Wait()
	d.Dispatch() // final drain

	if n := len(sink.calls); n != producers*perProducer {
		t.Fatalf("delivered %d records, want %d (each exactly once)", n, producers*perProducer)
	}
	for p := 0; p < producers; p++ {
		key := fmt.Sprintf("key-%d", p)
		got := sink.byKey[key]
		want := make([]string, perProducer)
		for i := range want {
			want[i] = fmt.Sprintf("r-%d-%d", p, i)
		}
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("per-key order for %s = %v", key, got)
		}
	}
}
