package vcfarchitecture

import "errors"

// BuildPlan constructs the ordered migration architecture for inventory using
// only combinations and gates present in the pinned compatibility snapshot.
func BuildPlan(inventory Inventory, compatibility CompatibilitySnapshot) (MigrationPlan, error) {
	return MigrationPlan{}, errors.New("BuildPlan is not implemented")
}
