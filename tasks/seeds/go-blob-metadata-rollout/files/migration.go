package blobrollout

import (
	"context"
	"fmt"
)

const (
	contentTypeKey = "content-type"
	checksumKey    = "sha256"
)

type Migrator struct {
	store              Store
	pageSize           int
	maxConflictRetries int
}

func NewMigrator(
	store Store,
	pageSize int,
	maxConflictRetries int,
) (*Migrator, error) {
	if store == nil {
		return nil, fmt.Errorf("store is required")
	}
	if pageSize <= 0 {
		return nil, fmt.Errorf("page size must be positive")
	}
	if maxConflictRetries < 0 {
		return nil, fmt.Errorf("max conflict retries cannot be negative")
	}
	return &Migrator{
		store:              store,
		pageSize:           pageSize,
		maxConflictRetries: maxConflictRetries,
	}, nil
}

func (m *Migrator) Run(
	ctx context.Context,
	rolloutID string,
) (Stats, error) {
	var stats Stats
	if rolloutID == "" {
		return stats, fmt.Errorf("rollout id is required")
	}

	checkpoint, err := m.store.LoadCheckpoint(ctx, rolloutID)
	if err != nil {
		return stats, fmt.Errorf("load checkpoint: %w", err)
	}
	if checkpoint.Complete {
		return stats, nil
	}

	// BUG: a restart incorrectly begins at the first page.
	cursor := ""
	for {
		page, err := m.store.ListPage(ctx, cursor, m.pageSize)
		if err != nil {
			return stats, fmt.Errorf("list page: %w", err)
		}

		complete := page.NextCursor == ""
		// BUG: recording the page boundary before its blobs can skip failed work.
		if err := m.store.SaveCheckpoint(
			ctx,
			rolloutID,
			checkpoint.Version,
			page.NextCursor,
			complete,
		); err != nil {
			return stats, fmt.Errorf("save checkpoint: %w", err)
		}
		checkpoint.Version++
		stats.Pages++

		for _, blob := range page.Blobs {
			stats.Scanned++
			if blob.Metadata == nil {
				blob.Metadata = make(map[string]string)
			}
			// BUG: listed values and maps are stale and existing keys are replaced.
			blob.Metadata[contentTypeKey] = blob.ContentType
			blob.Metadata[checksumKey] = blob.Checksum
			if err := m.store.ReplaceMetadata(
				ctx,
				blob.Key,
				blob.Version,
				blob.Metadata,
			); err != nil {
				return stats, fmt.Errorf("replace metadata for %q: %w", blob.Key, err)
			}
			stats.Updated++
		}
		if complete {
			return stats, nil
		}
		cursor = page.NextCursor
	}
}
