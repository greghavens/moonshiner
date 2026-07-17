package pubsub

import (
	"fmt"
	"testing"
)

// recv pops one already-buffered message; Publish guarantees delivery
// into subscriber buffers before it returns, so no waiting is needed.
func recv(t *testing.T, s *Subscription) Message {
	t.Helper()
	select {
	case m, ok := <-s.C():
		if !ok {
			t.Fatal("subscription channel closed unexpectedly")
		}
		return m
	default:
		t.Fatal("expected a buffered message, channel is empty")
	}
	return Message{}
}

func TestPublishDeliversTopicAndPayload(t *testing.T) {
	b := NewBus()
	sub, err := b.Subscribe("orders.created", 4)
	if err != nil {
		t.Fatalf("Subscribe: %v", err)
	}
	b.Publish("orders.created", "order #1001")
	m := recv(t, sub)
	if m.Topic != "orders.created" || m.Payload != "order #1001" {
		t.Fatalf("got %+v, want topic orders.created payload order #1001", m)
	}
}

func TestEverySubscriberOnATopicReceives(t *testing.T) {
	b := NewBus()
	var subs []*Subscription
	for i := 0; i < 3; i++ {
		s, err := b.Subscribe("billing.charged", 2)
		if err != nil {
			t.Fatalf("Subscribe #%d: %v", i, err)
		}
		subs = append(subs, s)
	}
	b.Publish("billing.charged", "invoice 7")
	for i, s := range subs {
		if m := recv(t, s); m.Payload != "invoice 7" {
			t.Fatalf("subscriber %d got %+v", i, m)
		}
	}
}

func TestOtherTopicsDoNotLeakIn(t *testing.T) {
	b := NewBus()
	sub, err := b.Subscribe("jobs", 4)
	if err != nil {
		t.Fatalf("Subscribe: %v", err)
	}
	b.Publish("jobs.retry", "not for the exact-topic sub")
	b.Publish("metrics", "also not")
	if n := len(sub.C()); n != 0 {
		t.Fatalf("subscriber on %q buffered %d messages, want 0", sub.Topic(), n)
	}
	b.Publish("jobs", "yes")
	if m := recv(t, sub); m.Payload != "yes" {
		t.Fatalf("got %+v, want payload yes", m)
	}
}

func TestDeliveryOrderIsPublishOrder(t *testing.T) {
	b := NewBus()
	sub, err := b.Subscribe("audit", 8)
	if err != nil {
		t.Fatalf("Subscribe: %v", err)
	}
	for i := 0; i < 5; i++ {
		b.Publish("audit", fmt.Sprintf("event-%d", i))
	}
	for i := 0; i < 5; i++ {
		if m := recv(t, sub); m.Payload != fmt.Sprintf("event-%d", i) {
			t.Fatalf("message %d out of order: %+v", i, m)
		}
	}
}

func TestUnsubscribeStopsDelivery(t *testing.T) {
	b := NewBus()
	sub, err := b.Subscribe("alerts", 4)
	if err != nil {
		t.Fatalf("Subscribe: %v", err)
	}
	b.Publish("alerts", "before")
	sub.Unsubscribe()
	b.Publish("alerts", "after")
	if m := recv(t, sub); m.Payload != "before" {
		t.Fatalf("buffered message = %+v, want before", m)
	}
	if n := len(sub.C()); n != 0 {
		t.Fatalf("%d messages delivered after Unsubscribe, want 0", n)
	}
}

func TestSubscribeValidation(t *testing.T) {
	b := NewBus()
	if _, err := b.Subscribe("", 1); err == nil {
		t.Fatal("Subscribe with empty topic must error")
	}
	if _, err := b.Subscribe("ok", -1); err == nil {
		t.Fatal("Subscribe with negative buffer must error")
	}
}

func TestPublishWithNoSubscribersIsFine(t *testing.T) {
	b := NewBus()
	b.Publish("nobody.home", "hello?") // must not panic or block
}

func TestCloseClosesChannelsAndSilencesPublish(t *testing.T) {
	b := NewBus()
	sub, err := b.Subscribe("shutdown", 2)
	if err != nil {
		t.Fatalf("Subscribe: %v", err)
	}
	b.Publish("shutdown", "last call")
	b.Close()
	b.Close() // idempotent
	b.Publish("shutdown", "too late")

	if m, ok := <-sub.C(); !ok || m.Payload != "last call" {
		t.Fatalf("first receive = %+v ok=%v, want buffered last call", m, ok)
	}
	if _, ok := <-sub.C(); ok {
		t.Fatal("channel must be closed after bus Close")
	}
	if _, err := b.Subscribe("shutdown", 1); err == nil {
		t.Fatal("Subscribe on a closed bus must error")
	}
}
