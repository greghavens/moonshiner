package architecture

import "errors"

var ErrNotImplemented = errors.New("architecture planner is not implemented")

// Build derives a complete architecture from an inventory and a pinned
// compatibility snapshot.
func Build(Inventory, CompatibilitySnapshot) (Architecture, error) {
	return Architecture{}, ErrNotImplemented
}
