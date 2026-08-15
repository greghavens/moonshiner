package vcfarch

import "errors"

// BuildGreenfield returns the installer SddcSpec and the broader placement
// architecture derived from the protected requirements and compatibility data.
func BuildGreenfield(in Inputs) (SddcSpec, GreenfieldArchitecture, error) {
	return nil, GreenfieldArchitecture{}, errors.New("BuildGreenfield is not implemented")
}

// BuildMigrationPlan maps every estate component to the ordered, gated target
// declared by the protected compatibility snapshot.
func BuildMigrationPlan(in Inputs) (MigrationPlan, error) {
	return MigrationPlan{}, errors.New("BuildMigrationPlan is not implemented")
}
