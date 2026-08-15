package vcfarch

import "encoding/json"

// Inventory is the separately managed estate supplied by fixtures/estate.json.
type Inventory struct {
	EstateID     string      `json:"estateId"`
	TargetBundle string      `json:"targetBundle"`
	Components   []Component `json:"components"`
}

type Component struct {
	ID      string `json:"id"`
	Kind    string `json:"kind"`
	Name    string `json:"name"`
	Version string `json:"version"`
}

// CompatibilitySnapshot is the pinned, deterministic migration authority.
type CompatibilitySnapshot struct {
	SnapshotVersion string              `json:"snapshotVersion"`
	TargetBundle    string              `json:"targetBundle"`
	Rules           []CompatibilityRule `json:"rules"`
}

type CompatibilityRule struct {
	ComponentID   string   `json:"componentId"`
	SourceVersion string   `json:"sourceVersion"`
	TargetVersion string   `json:"targetVersion"`
	Strategy      string   `json:"strategy"`
	Gates         []string `json:"gates"`
}

type Architecture struct {
	SchemaVersion string          `json:"schemaVersion"`
	EstateID      string          `json:"estateId"`
	TargetBundle  string          `json:"targetBundle"`
	Greenfield    json.RawMessage `json:"greenfield"`
	MigrationPlan MigrationPlan   `json:"migrationPlan"`
}

type MigrationPlan struct {
	EstateID     string          `json:"estateId"`
	TargetBundle string          `json:"targetBundle"`
	Steps        []MigrationStep `json:"steps"`
}

type MigrationStep struct {
	Order         int      `json:"order"`
	ComponentID   string   `json:"componentId"`
	Kind          string   `json:"kind"`
	Name          string   `json:"name"`
	SourceVersion string   `json:"sourceVersion"`
	TargetVersion string   `json:"targetVersion"`
	Strategy      string   `json:"strategy"`
	Gates         []string `json:"gates"`
}
