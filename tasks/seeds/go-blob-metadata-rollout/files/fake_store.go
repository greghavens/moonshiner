package blobrollout

import (
	"context"
	"encoding/base64"
	"fmt"
	"sort"
	"strings"
	"sync"
)

// FakeStore is a concurrency-safe in-memory Store used by the deterministic
// rollout fixture. Every value crossing its API is copied.
type FakeStore struct {
	mu          sync.Mutex
	blobs       map[string]Blob
	checkpoints map[string]Checkpoint
}

func NewFakeStore(blobs []Blob) (*FakeStore, error) {
	store := &FakeStore{
		blobs:       make(map[string]Blob, len(blobs)),
		checkpoints: make(map[string]Checkpoint),
	}
	for _, blob := range blobs {
		if strings.TrimSpace(blob.Key) == "" {
			return nil, fmt.Errorf("blob key cannot be empty")
		}
		if blob.Version == 0 {
			return nil, fmt.Errorf("blob %q has zero version", blob.Key)
		}
		if _, exists := store.blobs[blob.Key]; exists {
			return nil, fmt.Errorf("duplicate blob key %q", blob.Key)
		}
		store.blobs[blob.Key] = cloneBlob(blob)
	}
	return store, nil
}

func (s *FakeStore) ListPage(
	ctx context.Context,
	cursor string,
	limit int,
) (Page, error) {
	if err := ctx.Err(); err != nil {
		return Page{}, err
	}
	if limit <= 0 {
		return Page{}, fmt.Errorf("page limit must be positive")
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	after, err := decodeCursor(cursor)
	if err != nil {
		return Page{}, err
	}
	keys := make([]string, 0, len(s.blobs))
	for key := range s.blobs {
		keys = append(keys, key)
	}
	sort.Strings(keys)

	start := sort.SearchStrings(keys, after)
	if cursor != "" {
		if start >= len(keys) || keys[start] != after {
			return Page{}, fmt.Errorf("%w: unknown position", ErrInvalidCursor)
		}
		start++
	}
	end := start + limit
	if end > len(keys) {
		end = len(keys)
	}
	page := Page{Blobs: make([]Blob, 0, end-start)}
	for _, key := range keys[start:end] {
		page.Blobs = append(page.Blobs, cloneBlob(s.blobs[key]))
	}
	if end < len(keys) {
		page.NextCursor = encodeCursor(keys[end-1])
	}
	return page, nil
}

func (s *FakeStore) GetBlob(ctx context.Context, key string) (Blob, error) {
	if err := ctx.Err(); err != nil {
		return Blob{}, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	blob, ok := s.blobs[key]
	if !ok {
		return Blob{}, fmt.Errorf("%w: %s", ErrBlobNotFound, key)
	}
	return cloneBlob(blob), nil
}

func (s *FakeStore) ReplaceMetadata(
	ctx context.Context,
	key string,
	expectedVersion uint64,
	metadata map[string]string,
) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	blob, ok := s.blobs[key]
	if !ok {
		return fmt.Errorf("%w: %s", ErrBlobNotFound, key)
	}
	if blob.Version != expectedVersion {
		return fmt.Errorf(
			"%w: blob %s is version %d, expected %d",
			ErrVersionConflict,
			key,
			blob.Version,
			expectedVersion,
		)
	}
	blob.Metadata = cloneMetadata(metadata)
	blob.Version++
	s.blobs[key] = blob
	return nil
}

func (s *FakeStore) LoadCheckpoint(
	ctx context.Context,
	rolloutID string,
) (Checkpoint, error) {
	if err := ctx.Err(); err != nil {
		return Checkpoint{}, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.checkpoints[rolloutID], nil
}

func (s *FakeStore) SaveCheckpoint(
	ctx context.Context,
	rolloutID string,
	expectedVersion uint64,
	cursor string,
	complete bool,
) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	current := s.checkpoints[rolloutID]
	if current.Version != expectedVersion {
		return fmt.Errorf(
			"%w: checkpoint is version %d, expected %d",
			ErrCheckpointConflict,
			current.Version,
			expectedVersion,
		)
	}
	s.checkpoints[rolloutID] = Checkpoint{
		Cursor:   cursor,
		Complete: complete,
		Version:  current.Version + 1,
	}
	return nil
}

// Snapshot returns a detached blob value for test setup and observation.
func (s *FakeStore) Snapshot(key string) (Blob, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	blob, ok := s.blobs[key]
	if !ok {
		return Blob{}, fmt.Errorf("%w: %s", ErrBlobNotFound, key)
	}
	return cloneBlob(blob), nil
}

// Mutate simulates a live writer. A successful mutation advances the blob's
// optimistic version exactly once.
func (s *FakeStore) Mutate(
	key string,
	mutation func(blob *Blob) error,
) error {
	if mutation == nil {
		return fmt.Errorf("mutation is nil")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	blob, ok := s.blobs[key]
	if !ok {
		return fmt.Errorf("%w: %s", ErrBlobNotFound, key)
	}
	detached := cloneBlob(blob)
	if err := mutation(&detached); err != nil {
		return err
	}
	detached.Key = blob.Key
	detached.Version = blob.Version + 1
	detached.Metadata = cloneMetadata(detached.Metadata)
	s.blobs[key] = detached
	return nil
}

func cloneBlob(blob Blob) Blob {
	blob.Metadata = cloneMetadata(blob.Metadata)
	return blob
}

func cloneMetadata(metadata map[string]string) map[string]string {
	if metadata == nil {
		return nil
	}
	copied := make(map[string]string, len(metadata))
	for key, value := range metadata {
		copied[key] = value
	}
	return copied
}

func encodeCursor(key string) string {
	return "opaque:" + base64.RawURLEncoding.EncodeToString([]byte(key))
}

func decodeCursor(cursor string) (string, error) {
	if cursor == "" {
		return "", nil
	}
	if !strings.HasPrefix(cursor, "opaque:") {
		return "", fmt.Errorf("%w: bad prefix", ErrInvalidCursor)
	}
	decoded, err := base64.RawURLEncoding.DecodeString(
		strings.TrimPrefix(cursor, "opaque:"),
	)
	if err != nil {
		return "", fmt.Errorf("%w: %v", ErrInvalidCursor, err)
	}
	return string(decoded), nil
}
