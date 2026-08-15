package planner

import "errors"

var ErrNotImplemented = errors.New("planner.Build is not implemented")

// Build creates a deterministic brownfield convergence architecture from the
// supplied estate and pinned compatibility snapshot.
func Build(_ Inventory, _ Snapshot) (Plan, error) {
	return Plan{}, ErrNotImplemented
}
