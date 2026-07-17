// Package exporter drains a device-event store into nightly archive
// batches.
package exporter

import (
	"context"
	"errors"
	"fmt"
)

// ErrExhausted is returned (possibly wrapped) by Session.Fetch when
// no batches remain in the stream.
var ErrExhausted = errors.New("event stream exhausted")

// Store opens export sessions against the event backend.
type Store interface {
	Open(ctx context.Context) (Session, error)
}

// Session streams event batches. Close must be called exactly once
// for every opened session, whatever the outcome of the export.
type Session interface {
	Fetch(ctx context.Context) ([]string, error)
	Close()
}

// Export drains every remaining batch from the store. It returns all
// events on success and an empty result when the stream was already
// exhausted. Any other outcome is an error — never partial data
// dressed up as success — and callers rely on errors.Is to recognize
// context cancellation and deadline expiry through whatever wrapping
// the store applies on the way up.
func Export(ctx context.Context, st Store) ([]string, error) {
	sess, err := st.Open(ctx)
	if err != nil {
		return nil, fmt.Errorf("open session: %w", err)
	}
	var events []string
	for {
		batch, err := sess.Fetch(ctx)
		if err != nil {
			break // stream drained
		}
		events = append(events, batch...)
	}
	sess.Close()
	return events, nil
}
