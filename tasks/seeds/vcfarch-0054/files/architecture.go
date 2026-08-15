package vcfarch

import "errors"

// Architecture is the machine-readable VCF design returned by Build.
// Implement the concrete JSON model required by the fixtures and schemas.
type Architecture struct{}

// Build constructs a complete greenfield design and existing-estate migration
// plan from the supplied fixture and pinned compatibility snapshot.
func Build(scenarioPath, estatePath, snapshotPath string) (Architecture, error) {
	return Architecture{}, errors.New("vcf architecture builder is not implemented")
}
