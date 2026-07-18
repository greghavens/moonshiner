// Package audit defines the account mutation evidence boundary.
package audit

import "context"

// Event is the stable record consumed by the compliance stream.
type Event struct {
	Action    string
	AccountID string
	Actor     string
	RequestID string
	Details   map[string]string
}

// Sink persists an event after its owning mutation succeeds.
type Sink interface {
	Record(context.Context, Event) error
}
