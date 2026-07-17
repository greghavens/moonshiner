// Package pubsub is the in-memory event bus the gateway process uses
// to fan events out to in-process handlers: a publisher names a topic
// like "orders.created" and every subscription on that topic gets the
// message in its buffered channel before Publish returns.
package pubsub

import (
	"errors"
	"fmt"
	"sync"
)

// Message is one published event.
type Message struct {
	Topic   string
	Payload string
}

// Bus routes published messages to subscriptions by topic.
type Bus struct {
	mu     sync.Mutex
	subs   map[string][]*Subscription
	closed bool
}

// Subscription is one subscriber's registration on a topic.
type Subscription struct {
	bus   *Bus
	topic string
	ch    chan Message
}

// NewBus returns an empty bus.
func NewBus() *Bus {
	return &Bus{subs: make(map[string][]*Subscription)}
}

// Subscribe registers a new subscription on topic with a delivery
// buffer of the given size. The topic must be non-empty and the
// buffer non-negative.
func (b *Bus) Subscribe(topic string, buffer int) (*Subscription, error) {
	if topic == "" {
		return nil, errors.New("pubsub: empty topic")
	}
	if buffer < 0 {
		return nil, fmt.Errorf("pubsub: negative buffer %d", buffer)
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.closed {
		return nil, errors.New("pubsub: bus is closed")
	}
	s := &Subscription{bus: b, topic: topic, ch: make(chan Message, buffer)}
	b.subs[topic] = append(b.subs[topic], s)
	return s, nil
}

// C returns the channel messages are delivered on. It is closed when
// the bus shuts down.
func (s *Subscription) C() <-chan Message { return s.ch }

// Topic reports the topic this subscription was registered with.
func (s *Subscription) Topic() string { return s.topic }

// Unsubscribe removes the subscription from the bus; no further
// messages are delivered. Messages already buffered may still be
// drained from C.
func (s *Subscription) Unsubscribe() {
	b := s.bus
	b.mu.Lock()
	defer b.mu.Unlock()
	list := b.subs[s.topic]
	for i, other := range list {
		if other == s {
			b.subs[s.topic] = append(list[:i:i], list[i+1:]...)
			break
		}
	}
	if len(b.subs[s.topic]) == 0 {
		delete(b.subs, s.topic)
	}
}

// Publish delivers payload to every subscription on topic. By the
// time Publish returns the message sits in each subscriber's buffer;
// if a buffer is full, Publish waits for the subscriber to make room.
// Publishing on a closed bus is a no-op.
func (b *Bus) Publish(topic, payload string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.closed {
		return
	}
	msg := Message{Topic: topic, Payload: payload}
	for _, s := range b.subs[topic] {
		s.ch <- msg
	}
}

// Close shuts the bus down: every subscription channel is closed so
// consumer range loops terminate, and later Publish calls are
// discarded. Close is idempotent.
func (b *Bus) Close() {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.closed {
		return
	}
	b.closed = true
	for _, list := range b.subs {
		for _, s := range list {
			close(s.ch)
		}
	}
	b.subs = make(map[string][]*Subscription)
}
