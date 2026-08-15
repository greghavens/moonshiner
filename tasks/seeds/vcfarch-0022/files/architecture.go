package vcfarchitecture

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

// Build returns the installer SddcSpec and the ordered estate migration plan.
func Build(scenario Scenario, estate Estate, authority CompatibilitySnapshot) (map[string]any, MigrationPlan, error) {
	return nil, MigrationPlan{}, errors.New("TODO: implement the VCF architecture")
}

func LoadJSON(path string, dst any) error {
	b, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("read %s: %w", path, err)
	}
	if err := json.Unmarshal(b, dst); err != nil {
		return fmt.Errorf("decode %s: %w", path, err)
	}
	return nil
}

// WriteArtifacts writes stable, indented JSON files under artifacts/.
func WriteArtifacts(root string, spec map[string]any, plan MigrationPlan) error {
	outDir := filepath.Join(root, "artifacts")
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return err
	}
	for name, value := range map[string]any{
		"sddc-spec.json":      spec,
		"migration-plan.json": plan,
	} {
		b, err := json.MarshalIndent(value, "", "  ")
		if err != nil {
			return fmt.Errorf("encode %s: %w", name, err)
		}
		b = append(b, '\n')
		if err := os.WriteFile(filepath.Join(outDir, name), b, 0o644); err != nil {
			return fmt.Errorf("write %s: %w", name, err)
		}
	}
	return nil
}
