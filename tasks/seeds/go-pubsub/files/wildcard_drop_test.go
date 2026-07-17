package pubsub

import (
	"fmt"
	"sync"
	"testing"
)

// Acceptance tests for wildcard topic subscriptions ("orders.*"), the
// drop-newest policy for slow subscribers, and per-subscription
// delivery/drop counters exposed via Stats.

func mustSub(t *testing.T, b *Bus, pattern string, buffer int) *Subscription {
	t.Helper()
	s, err := b.Subscribe(pattern, buffer)
	if err != nil {
		t.Fatalf("Subscribe(%q, %d): %v", pattern, buffer, err)
	}
	return s
}

func TestWildcardMatchesExactlyOneSegment(t *testing.T) {
	b := NewBus()
	sub := mustSub(t, b, "orders.*", 8)

	b.Publish("orders.created", "a")
	b.Publish("orders.paid", "b")
	if got := recv(t, sub).Payload; got != "a" {
		t.Fatalf("first match = %q, want a", got)
	}
	if got := recv(t, sub).Payload; got != "b" {
		t.Fatalf("second match = %q, want b", got)
	}

	b.Publish("orders", "bare parent must not match")
	b.Publish("orders.eu.created", "two segments under orders must not match")
	b.Publish("billing.created", "other prefix must not match")
	if n := len(sub.C()); n != 0 {
		t.Fatalf("orders.* buffered %d non-matching messages, want 0", n)
	}
}

func TestWildcardWorksInAnySegmentPosition(t *testing.T) {
	b := NewBus()
	lead := mustSub(t, b, "*.created", 8)
	mid := mustSub(t, b, "orders.*.audit", 8)

	b.Publish("orders.created", "x")
	b.Publish("billing.created", "y")
	b.Publish("orders.paid", "no")
	b.Publish("created", "no")
	if n := len(lead.C()); n != 2 {
		t.Fatalf("*.created buffered %d messages, want 2", n)
	}
	if got := recv(t, lead).Payload; got != "x" {
		t.Fatalf("*.created first = %q, want x", got)
	}
	if got := recv(t, lead).Payload; got != "y" {
		t.Fatalf("*.created second = %q, want y", got)
	}

	b.Publish("orders.eu.audit", "z")
	b.Publish("orders.audit", "no")
	b.Publish("orders.eu.fr.audit", "no")
	if n := len(mid.C()); n != 1 {
		t.Fatalf("orders.*.audit buffered %d messages, want 1", n)
	}
	if got := recv(t, mid).Payload; got != "z" {
		t.Fatalf("orders.*.audit got %q, want z", got)
	}
}

func TestExactAndWildcardSubscribersEachReceiveOnce(t *testing.T) {
	b := NewBus()
	exact := mustSub(t, b, "orders.created", 4)
	wild := mustSub(t, b, "orders.*", 4)

	b.Publish("orders.created", "o-1")
	if n := len(exact.C()); n != 1 {
		t.Fatalf("exact subscriber buffered %d, want exactly 1", n)
	}
	if n := len(wild.C()); n != 1 {
		t.Fatalf("wildcard subscriber buffered %d, want exactly 1", n)
	}
	if got := recv(t, exact).Payload; got != "o-1" {
		t.Fatalf("exact got %q", got)
	}
	if got := recv(t, wild).Payload; got != "o-1" {
		t.Fatalf("wildcard got %q", got)
	}
}

func TestWildcardMustBeAWholeSegment(t *testing.T) {
	b := NewBus()
	for _, pattern := range []string{"orders.cre*", "or*ers.created", "orders.**", "*sales"} {
		if _, err := b.Subscribe(pattern, 1); err == nil {
			t.Fatalf("Subscribe(%q) must reject a partial-segment wildcard", pattern)
		}
	}
	if _, err := b.Subscribe("orders.*", 1); err != nil {
		t.Fatalf("Subscribe(orders.*) must be accepted, got %v", err)
	}
}

func TestPublishNeverBlocksOnAFullBufferAndDropsNewest(t *testing.T) {
	b := NewBus()
	sub := mustSub(t, b, "firehose", 2)
	for i := 0; i < 5; i++ {
		b.Publish("firehose", fmt.Sprintf("event-%d", i)) // must return promptly even when full
	}
	if got, want := sub.Stats(), (Stats{Delivered: 2, Dropped: 3}); got != want {
		t.Fatalf("Stats = %+v, want %+v", got, want)
	}
	if got := recv(t, sub).Payload; got != "event-0" {
		t.Fatalf("first kept message = %q, want event-0 (oldest kept, newest dropped)", got)
	}
	if got := recv(t, sub).Payload; got != "event-1" {
		t.Fatalf("second kept message = %q, want event-1", got)
	}
	if n := len(sub.C()); n != 0 {
		t.Fatalf("%d extra messages buffered, want 0", n)
	}
}

func TestOneSlowSubscriberDoesNotStarveTheOthers(t *testing.T) {
	b := NewBus()
	slow := mustSub(t, b, "ticks", 1)
	fast := mustSub(t, b, "ticks", 8)
	for i := 0; i < 4; i++ {
		b.Publish("ticks", fmt.Sprintf("t%d", i))
	}
	if got, want := fast.Stats(), (Stats{Delivered: 4, Dropped: 0}); got != want {
		t.Fatalf("fast Stats = %+v, want %+v", got, want)
	}
	for i := 0; i < 4; i++ {
		if got := recv(t, fast).Payload; got != fmt.Sprintf("t%d", i) {
			t.Fatalf("fast message %d = %q", i, got)
		}
	}
	if got, want := slow.Stats(), (Stats{Delivered: 1, Dropped: 3}); got != want {
		t.Fatalf("slow Stats = %+v, want %+v", got, want)
	}
	if got := recv(t, slow).Payload; got != "t0" {
		t.Fatalf("slow kept %q, want t0", got)
	}
}

func TestZeroBufferSubscriberWithNoReaderDropsEverything(t *testing.T) {
	b := NewBus()
	sub := mustSub(t, b, "noisy", 0)
	for i := 0; i < 3; i++ {
		b.Publish("noisy", "x")
	}
	if got, want := sub.Stats(), (Stats{Delivered: 0, Dropped: 3}); got != want {
		t.Fatalf("Stats = %+v, want %+v", got, want)
	}
}

func TestStatsStartAtZeroAndCountWildcardDeliveries(t *testing.T) {
	b := NewBus()
	wild := mustSub(t, b, "jobs.*", 4)
	if got := wild.Stats(); got != (Stats{}) {
		t.Fatalf("fresh subscription Stats = %+v, want zeros", got)
	}
	b.Publish("jobs.retry", "r")
	b.Publish("jobs.done", "d")
	b.Publish("cron.done", "not matched, not counted")
	if got, want := wild.Stats(), (Stats{Delivered: 2, Dropped: 0}); got != want {
		t.Fatalf("Stats = %+v, want %+v", got, want)
	}
}

func TestConcurrentPublishersAreRaceCleanAndFullyAccounted(t *testing.T) {
	const publishers, perPublisher = 8, 50
	const total = publishers * perPublisher

	b := NewBus()
	slow := mustSub(t, b, "load.spike", 4)
	fast := mustSub(t, b, "load.spike", total)
	wild := mustSub(t, b, "load.*", total)

	var wg sync.WaitGroup
	for p := 0; p < publishers; p++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := 0; i < perPublisher; i++ {
				b.Publish("load.spike", "m")
			}
		}()
	}
	wg.Wait()

	if got, want := fast.Stats(), (Stats{Delivered: total, Dropped: 0}); got != want {
		t.Fatalf("fast Stats = %+v, want %+v", got, want)
	}
	if got, want := wild.Stats(), (Stats{Delivered: total, Dropped: 0}); got != want {
		t.Fatalf("wildcard Stats = %+v, want %+v", got, want)
	}
	got := slow.Stats()
	if got.Delivered != 4 || got.Dropped != total-4 {
		t.Fatalf("slow Stats = %+v, want Delivered 4 (buffer size, never drained) Dropped %d", got, total-4)
	}
}
