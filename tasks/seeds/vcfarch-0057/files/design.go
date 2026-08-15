package vcfarch

import "errors"

// Architecture is the machine-readable VCF design returned by Build.
// Implement the concrete JSON model required by the fixtures and schemas.
type Architecture struct{}

// Build constructs the greenfield SddcSpec and the existing-estate migration
// plan from the supplied scenario, inventory, and pinned compatibility snapshot.
func Build(scenarioPath, estatePath, snapshotPath string) (Architecture, error) {
	return Architecture{}, errors.New("VCF architecture builder is not implemented")
}
