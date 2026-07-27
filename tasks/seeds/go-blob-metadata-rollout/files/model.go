package blobrollout

import (
	"context"
	"errors"
)

var (
	ErrVersionConflict    = errors.New("blob version conflict")
	ErrCheckpointConflict = errors.New("checkpoint version conflict")
	ErrBlobNotFound       = errors.New("blob not found")
	ErrInvalidCursor      = errors.New("invalid cursor")
)

// Blob is a point-in-time value returned by the store. Metadata is owned by
// the store: callers must treat maps returned by ListPage and GetBlob as
// immutable.
type Blob struct {
	Key         string
	Version     uint64
	ContentType string
	Checksum    string
	Metadata    map[string]string
}

// Page contains blobs after the requested opaque cursor.
type Page struct {
	Blobs      []Blob
	NextCursor string
}

// Checkpoint identifies the next page to process. Version is the optimistic
// version used when persisting a page boundary.
type Checkpoint struct {
	Cursor   string
	Complete bool
	Version  uint64
}

// Store is the persistence boundary used by the rollout.
type Store interface {
	ListPage(ctx context.Context, cursor string, limit int) (Page, error)
	GetBlob(ctx context.Context, key string) (Blob, error)
	ReplaceMetadata(
		ctx context.Context,
		key string,
		expectedVersion uint64,
		metadata map[string]string,
	) error
	LoadCheckpoint(ctx context.Context, rolloutID string) (Checkpoint, error)
	SaveCheckpoint(
		ctx context.Context,
		rolloutID string,
		expectedVersion uint64,
		cursor string,
		complete bool,
	) error
}

// Stats describes work performed by one Run call.
type Stats struct {
	Pages     int
	Scanned   int
	Updated   int
	Unchanged int
	Conflicts int
}
