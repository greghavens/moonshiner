package migrationplan

import "errors"

var ErrNotImplemented = errors.New("migration plan builder not implemented")

// Build reads the estate inventory and frozen compatibility snapshot and returns
// the complete, ordered migration architecture.
func Build(inventoryPath, snapshotPath string) (Plan, error) {
	return Plan{}, ErrNotImplemented
}
