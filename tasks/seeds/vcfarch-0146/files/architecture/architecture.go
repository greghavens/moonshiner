package architecture

import (
	"encoding/json"
	"errors"
	"os"
)

// Artifact is both an installer SddcSpec and the machine-readable extension
// used to describe the existing-estate convergence.
type Artifact struct {
	SddcID        string           `json:"sddcId"`
	WorkflowType  string           `json:"workflowType,omitempty"`
	HostSpecs     []map[string]any `json:"hostSpecs,omitempty"`
	Version       string           `json:"version,omitempty"`
	VcenterSpec   map[string]any   `json:"vcenterSpec"`
	ClusterSpec   map[string]any   `json:"clusterSpec,omitempty"`
	DvsSpecs      []map[string]any `json:"dvsSpecs,omitempty"`
	NsxtSpec      map[string]any   `json:"nsxtSpec,omitempty"`
	NetworkSpecs  []map[string]any `json:"networkSpecs"`
	DnsSpec       map[string]any   `json:"dnsSpec"`
	NtpServers    []string         `json:"ntpServers,omitempty"`
	SchemaVersion string           `json:"schemaVersion"`
	FleetID       string           `json:"fleetId"`
	Management    ManagedDomain    `json:"managementDomain"`
	MigrationPlan MigrationPlan    `json:"migrationPlan"`
}

type ManagedDomain struct {
	DomainID    string             `json:"domainId"`
	Disposition string             `json:"disposition"`
	Components  []ManagedComponent `json:"components"`
}

type ManagedComponent struct {
	ComponentID    string   `json:"componentId"`
	Type           string   `json:"type"`
	CurrentVersion string   `json:"currentVersion"`
	TargetVersion  string   `json:"targetVersion"`
	Action         string   `json:"action"`
	Gates          []string `json:"gates"`
}

type MigrationPlan struct {
	DomainID        string           `json:"domainId"`
	TargetVersion   string           `json:"targetVersion"`
	Steps           []MigrationStep  `json:"steps"`
	FinalComponents []FinalComponent `json:"finalComponents"`
}

type MigrationStep struct {
	Order      int                   `json:"order"`
	Action     string                `json:"action"`
	Components []ComponentTransition `json:"components"`
	Gates      []string              `json:"gates"`
}

type ComponentTransition struct {
	ComponentID string `json:"componentId"`
	Type        string `json:"type"`
	FromVersion string `json:"fromVersion"`
	ToVersion   string `json:"toVersion"`
}

type FinalComponent struct {
	ComponentID    string   `json:"componentId"`
	Type           string   `json:"type"`
	CurrentVersion string   `json:"currentVersion"`
	TargetVersion  string   `json:"targetVersion"`
	Gates          []string `json:"gates"`
}

// Build creates the architecture from the protected inventory and compatibility
// snapshot. It is intentionally unfinished in the starter workspace.
func Build(inventoryPath, compatibilityPath string) (Artifact, error) {
	return Artifact{}, errors.New("architecture.Build is not implemented")
}

func WriteFile(path string, artifact Artifact) error {
	data, err := json.MarshalIndent(artifact, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return os.WriteFile(path, data, 0o644)
}
