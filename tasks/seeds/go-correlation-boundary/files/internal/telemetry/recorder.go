package telemetry

import (
	"context"
	"sync"

	"example.com/correlation-boundary/internal/correlation"
)

type Entry struct {
	Event         string
	CorrelationID string
	Account       string
}

// Recorder is a small concurrency-safe structured-log sink used by the service.
type Recorder struct {
	mu      sync.Mutex
	entries []Entry
}

func (r *Recorder) Record(ctx context.Context, event, account string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.entries = append(r.entries, Entry{
		Event:         event,
		CorrelationID: correlation.FromContext(ctx),
		Account:       account,
	})
}

func (r *Recorder) Entries() []Entry {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]Entry(nil), r.entries...)
}
