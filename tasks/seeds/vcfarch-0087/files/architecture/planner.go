package architecture

import "errors"

// Build creates the deterministic ordered migration architecture described by
// the inventory and compatibility snapshot.
func Build(Inventory, CompatibilitySnapshot) (Plan, error) {
	return Plan{}, errors.New("migration architecture is not implemented")
}

// Validate checks a plan against the inventory and pinned compatibility
// snapshot. JSON-schema and installer-schema validation are verifier concerns.
func Validate(Plan, Inventory, CompatibilitySnapshot) error {
	return errors.New("migration architecture validation is not implemented")
}
