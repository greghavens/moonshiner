package architecture

import "fmt"

// Build creates the deterministic architecture artifact from the supplied
// scenario and pinned compatibility authority.
func Build(scenarioPath, snapshotPath string) (Artifact, error) {
	return Artifact{}, fmt.Errorf("architecture.Build is not implemented")
}
