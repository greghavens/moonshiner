package eventbackfill

import (
	"context"
	"encoding/base64"
	"fmt"
	"strconv"
	"strings"
	"sync"
)

// FakeStore is an in-memory implementation used by the offline verification.
// It deliberately emits non-semantic cursors and clones all byte slices.
type FakeStore struct {
	mu sync.Mutex

	order       []string
	events      map[string]Event
	checkpoints map[string]Checkpoint
	operations  map[string]string
	rewrites    map[string]int
}

func NewFakeStore(events []Event) (*FakeStore, error) {
	store := &FakeStore{
		events:      make(map[string]Event, len(events)),
		checkpoints: make(map[string]Checkpoint),
		operations:  make(map[string]string),
		rewrites:    make(map[string]int),
	}
	for _, event := range events {
		if event.ID == "" {
			return nil, fmt.Errorf("empty event ID")
		}
		if event.SchemaVersion != 1 && event.SchemaVersion != 2 {
			return nil, fmt.Errorf(
				"event %q: %w: %d",
				event.ID,
				ErrUnsupportedVersion,
				event.SchemaVersion,
			)
		}
		if event.Revision == 0 {
			return nil, fmt.Errorf("event %q has zero revision", event.ID)
		}
		if _, exists := store.events[event.ID]; exists {
			return nil, fmt.Errorf("duplicate event ID %q", event.ID)
		}
		store.order = append(store.order, event.ID)
		store.events[event.ID] = cloneEvent(event)
	}
	return store, nil
}

func cloneEvent(event Event) Event {
	event.Payload = append([]byte(nil), event.Payload...)
	return event
}

func encodeToken(index int) string {
	raw := base64.RawURLEncoding.EncodeToString(
		[]byte(fmt.Sprintf("position=%d;scope=events/v2", index)),
	)
	return "cursor::" + raw + "::opaque"
}

func decodeToken(token string) (int, error) {
	if token == "" {
		return 0, nil
	}
	const prefix = "cursor::"
	const suffix = "::opaque"
	if !strings.HasPrefix(token, prefix) || !strings.HasSuffix(token, suffix) {
		return 0, fmt.Errorf("invalid resume token")
	}
	encoded := strings.TrimSuffix(strings.TrimPrefix(token, prefix), suffix)
	decoded, err := base64.RawURLEncoding.DecodeString(encoded)
	if err != nil {
		return 0, fmt.Errorf("decode resume token: %w", err)
	}
	text := string(decoded)
	const position = "position="
	const trailer = ";scope=events/v2"
	if !strings.HasPrefix(text, position) || !strings.HasSuffix(text, trailer) {
		return 0, fmt.Errorf("invalid resume token body")
	}
	value := strings.TrimSuffix(strings.TrimPrefix(text, position), trailer)
	index, err := strconv.Atoi(value)
	if err != nil || index < 0 {
		return 0, fmt.Errorf("invalid resume token position")
	}
	return index, nil
}

func (s *FakeStore) LoadCheckpoint(
	ctx context.Context,
	migrationID string,
) (Checkpoint, error) {
	if err := ctx.Err(); err != nil {
		return Checkpoint{}, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.checkpoints[migrationID], nil
}

func (s *FakeStore) ListBatch(
	ctx context.Context,
	token string,
	limit int,
) (Batch, error) {
	if err := ctx.Err(); err != nil {
		return Batch{}, err
	}
	if limit <= 0 {
		return Batch{}, fmt.Errorf("limit must be positive")
	}
	start, err := decodeToken(token)
	if err != nil {
		return Batch{}, err
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	if start > len(s.order) {
		return Batch{}, fmt.Errorf("resume token is past the end")
	}
	end := start + limit
	if end > len(s.order) {
		end = len(s.order)
	}
	events := make([]Event, 0, end-start)
	for _, id := range s.order[start:end] {
		events = append(events, cloneEvent(s.events[id]))
	}
	return Batch{
		Events:    events,
		NextToken: encodeToken(end),
		Complete:  end == len(s.order),
	}, nil
}

func (s *FakeStore) GetEvent(
	ctx context.Context,
	eventID string,
) (Event, error) {
	if err := ctx.Err(); err != nil {
		return Event{}, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	event, exists := s.events[eventID]
	if !exists {
		return Event{}, fmt.Errorf("%w: %s", ErrNotFound, eventID)
	}
	return cloneEvent(event), nil
}

func (s *FakeStore) RewriteV2(
	ctx context.Context,
	eventID string,
	expectedRevision uint64,
	operationID string,
	payload []byte,
) (RewriteResult, error) {
	if err := ctx.Err(); err != nil {
		return RewriteResult{}, err
	}
	if operationID == "" {
		return RewriteResult{}, fmt.Errorf("empty rewrite operation ID")
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	if priorEvent, exists := s.operations[operationID]; exists {
		if priorEvent != eventID {
			return RewriteResult{}, fmt.Errorf(
				"operation ID was reused for %q and %q",
				priorEvent,
				eventID,
			)
		}
		return RewriteResult{Applied: false}, nil
	}
	event, exists := s.events[eventID]
	if !exists {
		return RewriteResult{}, fmt.Errorf("%w: %s", ErrNotFound, eventID)
	}
	if event.Revision != expectedRevision || event.SchemaVersion != 1 {
		return RewriteResult{}, ErrVersionConflict
	}
	event.SchemaVersion = 2
	event.Revision++
	event.Payload = append([]byte(nil), payload...)
	s.events[eventID] = event
	s.operations[operationID] = eventID
	s.rewrites[eventID]++
	return RewriteResult{Applied: true}, nil
}

func (s *FakeStore) SaveCheckpoint(
	ctx context.Context,
	migrationID string,
	expectedRevision uint64,
	token string,
	complete bool,
) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	current := s.checkpoints[migrationID]
	if current.Revision != expectedRevision {
		return ErrCheckpointConflict
	}
	s.checkpoints[migrationID] = Checkpoint{
		Token: token, Complete: complete, Revision: current.Revision + 1,
	}
	return nil
}

func (s *FakeStore) SeedCheckpoint(
	migrationID string,
	checkpoint Checkpoint,
) error {
	if migrationID == "" {
		return fmt.Errorf("empty migration ID")
	}
	if _, err := decodeToken(checkpoint.Token); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.checkpoints[migrationID] = checkpoint
	return nil
}

// Mutate simulates a coexisting writer and always advances Revision.
func (s *FakeStore) Mutate(
	eventID string,
	change func(*Event) error,
) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	event, exists := s.events[eventID]
	if !exists {
		return fmt.Errorf("%w: %s", ErrNotFound, eventID)
	}
	event = cloneEvent(event)
	if err := change(&event); err != nil {
		return err
	}
	event.Revision++
	s.events[eventID] = cloneEvent(event)
	return nil
}

func (s *FakeStore) Snapshot(eventID string) (Event, error) {
	return s.GetEvent(context.Background(), eventID)
}

func (s *FakeStore) Checkpoint(migrationID string) Checkpoint {
	value, _ := s.LoadCheckpoint(context.Background(), migrationID)
	return value
}

func (s *FakeStore) PhysicalRewrites(eventID string) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.rewrites[eventID]
}
