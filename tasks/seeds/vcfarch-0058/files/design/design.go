// Package design builds deterministic VCF architecture artifacts from the
// protected requirements, estate inventory, and compatibility snapshot.
package design

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

// Artifacts contains the two machine-verified architecture documents.
type Artifacts struct {
	SddcSpec      json.RawMessage
	MigrationPlan json.RawMessage
}

// Build derives the architecture artifacts from the supplied JSON documents.
func Build(requirements, estate, compatibility []byte) (Artifacts, error) {
	return Artifacts{}, errors.New("design: implementation required")
}

// Write writes both verified artifacts to dir.
func Write(dir string, artifacts Artifacts) error {
	if len(artifacts.SddcSpec) == 0 || len(artifacts.MigrationPlan) == 0 {
		return errors.New("design: both artifacts are required")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return fmt.Errorf("create artifact directory: %w", err)
	}
	outputs := []struct {
		name string
		data []byte
	}{
		{name: "sddc-spec.json", data: artifacts.SddcSpec},
		{name: "migration-plan.json", data: artifacts.MigrationPlan},
	}
	for _, output := range outputs {
		path := filepath.Join(dir, output.name)
		if err := os.WriteFile(path, output.data, 0o644); err != nil {
			return fmt.Errorf("write %s: %w", output.name, err)
		}
	}
	return nil
}
