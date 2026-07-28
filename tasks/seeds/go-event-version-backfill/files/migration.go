package eventbackfill

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
)

type Migrator struct {
	store              Store
	batchSize          int
	maxWriteRetries    int
	maxConflictRetries int
}

func NewMigrator(
	store Store,
	batchSize int,
	maxWriteRetries int,
	maxConflictRetries int,
) (*Migrator, error) {
	if store == nil {
		return nil, fmt.Errorf("nil store")
	}
	if batchSize <= 0 {
		return nil, fmt.Errorf("batch size must be positive")
	}
	if maxWriteRetries < 0 || maxConflictRetries < 0 {
		return nil, fmt.Errorf("retry counts cannot be negative")
	}
	return &Migrator{
		store:              store,
		batchSize:          batchSize,
		maxWriteRetries:    maxWriteRetries,
		maxConflictRetries: maxConflictRetries,
	}, nil
}

// RunBatch migrates at most one listed batch and then durably checkpoints its
// opaque continuation token.
func (m *Migrator) RunBatch(
	ctx context.Context,
	migrationID string,
) (Stats, error) {
	var stats Stats
	checkpoint, err := m.store.LoadCheckpoint(ctx, migrationID)
	if err != nil {
		return stats, fmt.Errorf("load checkpoint: %w", err)
	}
	if checkpoint.Complete {
		return stats, nil
	}

	// BUG: restarts discard the durable opaque token.
	batch, err := m.store.ListBatch(ctx, "", m.batchSize)
	if err != nil {
		return stats, fmt.Errorf("list batch: %w", err)
	}

	// BUG: deriving completion from token spelling and saving before the
	// rewrites can skip a partially applied batch.
	complete := batch.NextToken == ""
	if err := m.store.SaveCheckpoint(
		ctx,
		migrationID,
		checkpoint.Revision,
		batch.NextToken,
		complete,
	); err != nil {
		return stats, fmt.Errorf("save checkpoint: %w", err)
	}
	stats.Batches++

	// BUG: storage order is part of the event log contract.
	sort.Slice(batch.Events, func(i, j int) bool {
		return batch.Events[i].ID < batch.Events[j].ID
	})
	for _, event := range batch.Events {
		stats.Scanned++
		if event.SchemaVersion == 2 {
			stats.AlreadyV2++
			continue
		}

		payload, err := UpgradePayloadV1(event.Payload)
		if err != nil {
			return stats, fmt.Errorf("upgrade event %q: %w", event.ID, err)
		}
		operationID := fmt.Sprintf(
			"%s/%s/revision-%d",
			migrationID,
			event.ID,
			event.Revision,
		)
		if _, err := m.store.RewriteV2(
			ctx,
			event.ID,
			event.Revision,
			operationID,
			payload,
		); err != nil {
			return stats, fmt.Errorf("rewrite event %q: %w", event.ID, err)
		}
		stats.Rewritten++
	}
	return stats, nil
}

// UpgradePayloadV1 creates the additive v2 fields. The legacy fields must
// remain so an old reader can continue consuming newly migrated records.
func UpgradePayloadV1(payload []byte) ([]byte, error) {
	// BUG: decoding through a narrow struct drops legacy and unknown fields.
	var legacy struct {
		Type      string `json:"type"`
		Timestamp string `json:"timestamp"`
	}
	if err := json.Unmarshal(payload, &legacy); err != nil {
		return nil, fmt.Errorf("decode v1 payload: %w", err)
	}
	if legacy.Type == "" || legacy.Timestamp == "" {
		return nil, fmt.Errorf("v1 payload is missing type or timestamp")
	}
	current := struct {
		EventType  string `json:"event_type"`
		OccurredAt string `json:"occurred_at"`
	}{
		EventType: legacy.Type, OccurredAt: legacy.Timestamp,
	}
	upgraded, err := json.Marshal(current)
	if err != nil {
		return nil, fmt.Errorf("encode v2 payload: %w", err)
	}
	return upgraded, nil
}
