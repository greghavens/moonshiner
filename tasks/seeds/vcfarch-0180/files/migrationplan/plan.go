package migrationplan

import (
	"errors"
	"io"
)

// ErrNotImplemented marks the seed's intentionally incomplete baseline.
var ErrNotImplemented = errors.New("migration plan builder is not implemented")

// Build returns a deterministic migration-plan.json document from the estate
// inventory and the pinned installer snapshot.
func Build(inventory io.Reader, snapshot io.Reader) ([]byte, error) {
	return nil, ErrNotImplemented
}
