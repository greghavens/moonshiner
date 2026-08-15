// Package vcfarch loads a machine-readable VCF brownfield architecture.
package vcfarch

import (
	"errors"
)

// Architecture is the migration-plan.v1 document written to architecture/plan.json.
type Architecture struct {
	SchemaVersion  string          `json:"schemaVersion"`
	EstateID       string          `json:"estateId"`
	TargetVersion  string          `json:"targetVersion"`
	FoundationHops []FoundationHop `json:"foundationHops"`
	TargetSddcSpec map[string]any  `json:"targetSddcSpec"`
	Placements     []Placement     `json:"placements"`
	Steps          []MigrationStep `json:"steps"`
}

type FoundationHop struct {
	From  string   `json:"from"`
	To    string   `json:"to"`
	Gates []string `json:"gates"`
}

type Placement struct {
	ComponentID     string   `json:"componentId"`
	TargetProduct   string   `json:"targetProduct"`
	Domain          string   `json:"domain"`
	Network         string   `json:"network"`
	DeploymentModel string   `json:"deploymentModel"`
	NodeCount       int      `json:"nodeCount"`
	Size            string   `json:"size"`
	IPAddresses     []string `json:"ipAddresses"`
}

type RouteHop struct {
	Version   string `json:"version"`
	Operation string `json:"operation"`
}

type MigrationStep struct {
	Order         int        `json:"order"`
	ComponentID   string     `json:"componentId"`
	ComponentName string     `json:"componentName"`
	SourceVersion string     `json:"sourceVersion"`
	TargetProduct string     `json:"targetProduct"`
	TargetVersion string     `json:"targetVersion"`
	Action        string     `json:"action"`
	Gates         []string   `json:"gates"`
	Route         []RouteHop `json:"route"`
}

// Load reads and strictly decodes one architecture document.
func Load(path string) (*Architecture, error) {
	return nil, errors.New("TODO: implement Load")
}

// ValidateBasic checks document-level invariants that do not depend on the
// pinned compatibility snapshot.
func (a *Architecture) ValidateBasic() error {
	return errors.New("TODO: implement ValidateBasic")
}
