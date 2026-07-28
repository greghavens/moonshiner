package eventbackfill

import (
	"context"
	"errors"
)

var (
	ErrVersionConflict    = errors.New("event version conflict")
	ErrCheckpointConflict = errors.New("checkpoint version conflict")
	ErrTransient          = errors.New("transient store failure")
	ErrNotFound           = errors.New("event not found")
	ErrUnsupportedVersion = errors.New("unsupported event schema version")
	ErrBatchTooLarge      = errors.New("store returned an oversized batch")
)

// Event is the stored envelope. Payload is a JSON object. Revision is the
// store's optimistic-concurrency version; Sequence is the immutable stream
// position.
type Event struct {
	ID            string
	Stream        string
	Sequence      uint64
	SchemaVersion int
	Revision      uint64
	Payload       []byte
}

// Batch is in storage order. NextToken is opaque, including on a final batch;
// Complete, rather than the token's spelling, is the end-of-scan signal.
type Batch struct {
	Events    []Event
	NextToken string
	Complete  bool
}

type Checkpoint struct {
	Token    string
	Complete bool
	Revision uint64
}

// RewriteResult.Applied is false when operationID was already committed.
// Either result is a successful logical rewrite for that operation.
type RewriteResult struct {
	Applied bool
}

type Store interface {
	LoadCheckpoint(ctx context.Context, migrationID string) (Checkpoint, error)
	ListBatch(ctx context.Context, token string, limit int) (Batch, error)
	GetEvent(ctx context.Context, eventID string) (Event, error)
	RewriteV2(
		ctx context.Context,
		eventID string,
		expectedRevision uint64,
		operationID string,
		payload []byte,
	) (RewriteResult, error)
	SaveCheckpoint(
		ctx context.Context,
		migrationID string,
		expectedRevision uint64,
		token string,
		complete bool,
	) error
}

type Stats struct {
	Batches      int
	Scanned      int
	Rewritten    int
	AlreadyV2    int
	Conflicts    int
	WriteRetries int
}
