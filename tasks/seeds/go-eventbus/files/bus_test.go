package eventbus

import (
	"bytes"
	"fmt"
	"testing"
)

func mustPublish(t *testing.T, b *Bus, topic string, payload []byte) int64 {
	t.Helper()
	off, err := b.Publish(topic, payload)
	if err != nil {
		t.Fatalf("Publish(%q, %q): %v", topic, payload, err)
	}
	return off
}

func mustPoll(t *testing.T, b *Bus, topic, group string, max int) []Event {
	t.Helper()
	evs, err := b.Poll(topic, group, max)
	if err != nil {
		t.Fatalf("Poll(%q, %q, %d): %v", topic, group, max, err)
	}
	return evs
}

func mustAck(t *testing.T, b *Bus, topic, group string, through int64) {
	t.Helper()
	if err := b.Ack(topic, group, through); err != nil {
		t.Fatalf("Ack(%q, %q, %d): %v", topic, group, through, err)
	}
}

func wantEvents(t *testing.T, got []Event, wantOffsets []int64, wantPayloads []string) {
	t.Helper()
	if len(got) != len(wantOffsets) {
		t.Fatalf("got %d events (%+v), want %d", len(got), got, len(wantOffsets))
	}
	for i := range got {
		if got[i].Offset != wantOffsets[i] {
			t.Fatalf("event[%d].Offset = %d, want %d (full: %+v)", i, got[i].Offset, wantOffsets[i], got)
		}
		if string(got[i].Payload) != wantPayloads[i] {
			t.Fatalf("event[%d].Payload = %q, want %q", i, got[i].Payload, wantPayloads[i])
		}
	}
}

func TestPublishAssignsDensePerTopicOffsets(t *testing.T) {
	b := NewBus()
	if off := mustPublish(t, b, "orders", []byte("a")); off != 0 {
		t.Fatalf("first offset = %d, want 0", off)
	}
	if off := mustPublish(t, b, "orders", []byte("b")); off != 1 {
		t.Fatalf("second offset = %d, want 1", off)
	}
	if off := mustPublish(t, b, "invoices", []byte("x")); off != 0 {
		t.Fatalf("first offset on a different topic = %d, want 0 (offsets are per topic)", off)
	}
	if n := b.Len("orders"); n != 2 {
		t.Fatalf("Len(orders) = %d, want 2", n)
	}
	if n := b.Len("nope"); n != 0 {
		t.Fatalf("Len of unknown topic = %d, want 0", n)
	}
}

func TestPublishEmptyTopicRejected(t *testing.T) {
	b := NewBus()
	if _, err := b.Publish("", []byte("x")); err == nil {
		t.Fatal("Publish with empty topic must return an error")
	}
}

func TestPollWithoutAckRedelivers(t *testing.T) {
	b := NewBus()
	mustPublish(t, b, "orders", []byte("a"))
	mustPublish(t, b, "orders", []byte("b"))
	mustPublish(t, b, "orders", []byte("c"))

	first := mustPoll(t, b, "orders", "billing", 2)
	wantEvents(t, first, []int64{0, 1}, []string{"a", "b"})

	again := mustPoll(t, b, "orders", "billing", 2)
	wantEvents(t, again, []int64{0, 1}, []string{"a", "b"})
}

func TestAckAdvancesTheGroupCursor(t *testing.T) {
	b := NewBus()
	mustPublish(t, b, "orders", []byte("a"))
	mustPublish(t, b, "orders", []byte("b"))
	mustPublish(t, b, "orders", []byte("c"))

	mustPoll(t, b, "orders", "billing", 2)
	mustAck(t, b, "orders", "billing", 1)
	if pos := b.Position("orders", "billing"); pos != 2 {
		t.Fatalf("Position after Ack(1) = %d, want 2", pos)
	}

	rest := mustPoll(t, b, "orders", "billing", 10)
	wantEvents(t, rest, []int64{2}, []string{"c"})
	mustAck(t, b, "orders", "billing", 2)

	if evs := mustPoll(t, b, "orders", "billing", 10); len(evs) != 0 {
		t.Fatalf("Poll after acking everything = %+v, want empty", evs)
	}
}

func TestGroupsConsumeIndependently(t *testing.T) {
	b := NewBus()
	mustPublish(t, b, "orders", []byte("a"))
	mustPublish(t, b, "orders", []byte("b"))

	billing := mustPoll(t, b, "orders", "billing", 10)
	wantEvents(t, billing, []int64{0, 1}, []string{"a", "b"})
	mustAck(t, b, "orders", "billing", 1)

	// audit has never acked: it still starts from 0.
	audit := mustPoll(t, b, "orders", "audit", 10)
	wantEvents(t, audit, []int64{0, 1}, []string{"a", "b"})
	if pos := b.Position("orders", "audit"); pos != 0 {
		t.Fatalf("audit Position = %d, want 0 (acks of other groups must not leak)", pos)
	}
}

func TestSameGroupNameOnDifferentTopicsIsIndependent(t *testing.T) {
	b := NewBus()
	mustPublish(t, b, "orders", []byte("a"))
	mustPublish(t, b, "invoices", []byte("x"))
	mustPublish(t, b, "invoices", []byte("y"))

	mustPoll(t, b, "orders", "workers", 10)
	mustAck(t, b, "orders", "workers", 0)

	if pos := b.Position("invoices", "workers"); pos != 0 {
		t.Fatalf("Position(invoices, workers) = %d, want 0 (cursors are per topic+group)", pos)
	}
	evs := mustPoll(t, b, "invoices", "workers", 10)
	wantEvents(t, evs, []int64{0, 1}, []string{"x", "y"})
}

func TestSeekReplaysFromOffset(t *testing.T) {
	b := NewBus()
	for _, p := range []string{"a", "b", "c", "d"} {
		mustPublish(t, b, "orders", []byte(p))
	}
	mustPoll(t, b, "orders", "billing", 10)
	mustAck(t, b, "orders", "billing", 3)

	if err := b.Seek("orders", "billing", 1); err != nil {
		t.Fatalf("Seek: %v", err)
	}
	evs := mustPoll(t, b, "orders", "billing", 10)
	wantEvents(t, evs, []int64{1, 2, 3}, []string{"b", "c", "d"})

	// Seeking to the end is legal and yields an empty poll.
	if err := b.Seek("orders", "billing", 4); err != nil {
		t.Fatalf("Seek to end: %v", err)
	}
	if evs := mustPoll(t, b, "orders", "billing", 10); len(evs) != 0 {
		t.Fatalf("Poll after Seek(end) = %+v, want empty", evs)
	}

	if err := b.Seek("orders", "billing", 5); err == nil {
		t.Fatal("Seek past the end of the topic must return an error")
	}
	if err := b.Seek("orders", "billing", -1); err == nil {
		t.Fatal("Seek to a negative offset must return an error")
	}
}

func TestAckValidation(t *testing.T) {
	b := NewBus()
	mustPublish(t, b, "orders", []byte("a"))
	mustPublish(t, b, "orders", []byte("b"))

	if err := b.Ack("orders", "billing", 2); err != nil {
		// through == 2 is beyond the last published offset (1)
		t.Log("good: unseen offset rejected")
	} else {
		t.Fatal("Ack of an offset that was never published must return an error")
	}
	if pos := b.Position("orders", "billing"); pos != 0 {
		t.Fatalf("failed Ack must not move the cursor, Position = %d", pos)
	}
	if err := b.Ack("orders", "billing", -1); err == nil {
		t.Fatal("Ack of a negative offset must return an error")
	}

	mustAck(t, b, "orders", "billing", 1)
	// Re-acking older offsets is a harmless no-op, not a rewind.
	mustAck(t, b, "orders", "billing", 0)
	if pos := b.Position("orders", "billing"); pos != 2 {
		t.Fatalf("Position after re-acking an older offset = %d, want 2 (acks never move backwards)", pos)
	}
}

func TestPollValidation(t *testing.T) {
	b := NewBus()
	mustPublish(t, b, "orders", []byte("a"))

	if _, err := b.Poll("orders", "billing", 0); err == nil {
		t.Fatal("Poll with max < 1 must return an error")
	}
	if _, err := b.Poll("orders", "", 5); err == nil {
		t.Fatal("Poll with empty group must return an error")
	}
	evs, err := b.Poll("ghost-topic", "billing", 5)
	if err != nil {
		t.Fatalf("Poll on an unknown topic must not error, got: %v", err)
	}
	if len(evs) != 0 {
		t.Fatalf("Poll on unknown topic = %+v, want empty", evs)
	}
}

func TestPayloadsAreIsolated(t *testing.T) {
	b := NewBus()
	buf := []byte("original")
	mustPublish(t, b, "orders", buf)
	buf[0] = 'X' // publisher reuses its buffer

	evs := mustPoll(t, b, "orders", "billing", 1)
	if !bytes.Equal(evs[0].Payload, []byte("original")) {
		t.Fatalf("stored payload = %q, want %q (Publish must copy)", evs[0].Payload, "original")
	}

	evs[0].Payload[0] = 'Z' // consumer scribbles on the delivered slice
	again := mustPoll(t, b, "orders", "billing", 1)
	if !bytes.Equal(again[0].Payload, []byte("original")) {
		t.Fatalf("payload after consumer mutation = %q, want %q (Poll must hand out copies)", again[0].Payload, "original")
	}
}

func TestTopicsSorted(t *testing.T) {
	b := NewBus()
	mustPublish(t, b, "zeta", []byte("1"))
	mustPublish(t, b, "alpha", []byte("2"))
	mustPublish(t, b, "mid", []byte("3"))

	got := b.Topics()
	want := []string{"alpha", "mid", "zeta"}
	if len(got) != len(want) {
		t.Fatalf("Topics() = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("Topics() = %v, want %v (sorted ascending)", got, want)
		}
	}
}

func TestPollRespectsMaxAcrossManyEvents(t *testing.T) {
	b := NewBus()
	for i := 0; i < 10; i++ {
		mustPublish(t, b, "orders", []byte(fmt.Sprintf("e%d", i)))
	}
	evs := mustPoll(t, b, "orders", "billing", 3)
	wantEvents(t, evs, []int64{0, 1, 2}, []string{"e0", "e1", "e2"})
	mustAck(t, b, "orders", "billing", 2)

	evs = mustPoll(t, b, "orders", "billing", 4)
	wantEvents(t, evs, []int64{3, 4, 5, 6}, []string{"e3", "e4", "e5", "e6"})
}
