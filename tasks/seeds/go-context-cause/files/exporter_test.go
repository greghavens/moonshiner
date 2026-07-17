package exporter

import (
	"context"
	"errors"
	"fmt"
	"testing"
	"time"
)

// step scripts one Fetch call: deliver a batch or fail. after runs
// once the step has been served (e.g. to cancel the context between
// pages).
type step struct {
	batch []string
	err   error
	after func()
}

type scriptedSession struct {
	steps  []step
	next   int
	closed int
}

func (s *scriptedSession) Fetch(ctx context.Context) ([]string, error) {
	if err := ctx.Err(); err != nil {
		return nil, fmt.Errorf("query events: %w", err)
	}
	if s.next >= len(s.steps) {
		return nil, fmt.Errorf("cursor read: %w", ErrExhausted)
	}
	st := s.steps[s.next]
	s.next++
	if st.after != nil {
		defer st.after()
	}
	if st.err != nil {
		return nil, fmt.Errorf("cursor read: %w", st.err)
	}
	return st.batch, nil
}

func (s *scriptedSession) Close() { s.closed++ }

type scriptedStore struct{ sess *scriptedSession }

func (s *scriptedStore) Open(ctx context.Context) (Session, error) { return s.sess, nil }

func requireClosedOnce(t *testing.T, sess *scriptedSession) {
	t.Helper()
	if sess.closed != 1 {
		t.Fatalf("session Close called %d times, want exactly 1", sess.closed)
	}
}

func TestExportDrainsAllBatches(t *testing.T) {
	sess := &scriptedSession{steps: []step{
		{batch: []string{"evt-1", "evt-2"}},
		{batch: []string{"evt-3"}},
	}}
	events, err := Export(context.Background(), &scriptedStore{sess})
	if err != nil {
		t.Fatalf("Export: %v", err)
	}
	want := []string{"evt-1", "evt-2", "evt-3"}
	if len(events) != len(want) {
		t.Fatalf("events = %v, want %v", events, want)
	}
	for i := range want {
		if events[i] != want[i] {
			t.Fatalf("events = %v, want %v", events, want)
		}
	}
	requireClosedOnce(t, sess)
}

func TestExhaustedStreamIsEmptySuccess(t *testing.T) {
	sess := &scriptedSession{}
	events, err := Export(context.Background(), &scriptedStore{sess})
	if err != nil {
		t.Fatalf("an already-exhausted stream is a genuine empty success, got %v", err)
	}
	if len(events) != 0 {
		t.Fatalf("events = %v, want none", events)
	}
	requireClosedOnce(t, sess)
}

func TestCancellationBeforeFirstBatchIsNotSuccess(t *testing.T) {
	sess := &scriptedSession{steps: []step{{batch: []string{"evt-1"}}}}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	events, err := Export(ctx, &scriptedStore{sess})
	if err == nil {
		t.Fatalf("cancelled export reported success with %d events", len(events))
	}
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want context.Canceled identity through wrapping", err)
	}
	if len(events) != 0 {
		t.Fatalf("cancelled export returned data: %v", events)
	}
	requireClosedOnce(t, sess)
}

func TestCancellationMidStreamRejectsPartialProgress(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	sess := &scriptedSession{steps: []step{
		{batch: []string{"evt-1", "evt-2"}, after: cancel},
		{batch: []string{"evt-3"}},
	}}
	events, err := Export(ctx, &scriptedStore{sess})
	if err == nil {
		t.Fatalf("export cancelled mid-stream reported success with %d events", len(events))
	}
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want context.Canceled identity through wrapping", err)
	}
	if len(events) != 0 {
		t.Fatalf("cancelled export leaked partial progress: %v", events)
	}
	requireClosedOnce(t, sess)
}

func TestDeadlineIdentitySurvivesWrapping(t *testing.T) {
	sess := &scriptedSession{steps: []step{{batch: []string{"evt-1"}}}}
	ctx, cancel := context.WithDeadline(context.Background(), time.Unix(0, 0))
	defer cancel()
	events, err := Export(ctx, &scriptedStore{sess})
	if err == nil {
		t.Fatalf("expired export reported success with %d events", len(events))
	}
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("error = %v, want context.DeadlineExceeded identity through wrapping", err)
	}
	requireClosedOnce(t, sess)
}

func TestStorageFailureSurfaces(t *testing.T) {
	errReplica := errors.New("replica offline")
	sess := &scriptedSession{steps: []step{
		{batch: []string{"evt-1"}},
		{err: errReplica},
	}}
	events, err := Export(context.Background(), &scriptedStore{sess})
	if !errors.Is(err, errReplica) {
		t.Fatalf("error = %v, want the storage failure surfaced with identity intact", err)
	}
	if len(events) != 0 {
		t.Fatalf("failed export leaked partial progress: %v", events)
	}
	requireClosedOnce(t, sess)
}
