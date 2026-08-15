// Package architecture exposes the deployable VCF architecture artifact.
package architecture

import (
	"encoding/json"
	"errors"
)

// Design is the machine-readable architecture returned by Build and stored in
// design.json. SddcSpec remains raw JSON so its authoritative shape is the
// pinned VCF Installer OpenAPI schema rather than a second Go model.
type Design struct {
	SddcSpec  json.RawMessage `json:"sddcSpec"`
	Capacity  CapacityPlan    `json:"capacity"`
	Site      SitePlan        `json:"site"`
	Migration MigrationPlan   `json:"migrationPlan"`
	Research  []ResearchEntry `json:"research"`
}

type Resources struct {
	PhysicalCores    int     `json:"physicalCores"`
	MemoryGiB        int     `json:"memoryGiB"`
	RawStorageTiB    int     `json:"rawStorageTiB,omitempty"`
	UsableStorageTiB float64 `json:"usableStorageTiB,omitempty"`
}

type CapacityPlan struct {
	Required                  Resources `json:"required"`
	HostCount                 int       `json:"hostCount"`
	PerHost                   Resources `json:"perHost"`
	ReservedHostFailures      int       `json:"reservedHostFailures"`
	UsableStorageRatio        float64   `json:"usableStorageRatio"`
	ProvidedAfterHostFailures Resources `json:"providedAfterHostFailures"`
	ProvidedUsableStorageTiB  float64   `json:"providedUsableStorageTiB"`
}

type SitePlan struct {
	WorkloadSite        string  `json:"workloadSite"`
	ManagementSite      string  `json:"managementSite"`
	RackCount           int     `json:"rackCount"`
	HostsPerRack        int     `json:"hostsPerRack"`
	MaxRackFailures     int     `json:"maxRackFailures"`
	LatencyToManagement float64 `json:"latencyToManagementMs"`
	MaxAllowedLatency   float64 `json:"maxAllowedLatencyMs"`
	DataResidency       string  `json:"dataResidency"`
	Stretched           bool    `json:"stretched"`
}

type MigrationPlan struct {
	SchemaVersion          string          `json:"schemaVersion"`
	EstateID               string          `json:"estateId"`
	TargetVCFVersion       string          `json:"targetVcfVersion"`
	ManagementDomainChange bool            `json:"managementDomainChange"`
	Steps                  []MigrationStep `json:"steps"`
}

type MigrationStep struct {
	Order          int      `json:"order"`
	ComponentID    string   `json:"componentId"`
	Component      string   `json:"component"`
	CurrentVersion string   `json:"currentVersion"`
	Target         Target   `json:"target"`
	Action         string   `json:"action"`
	Gates          []string `json:"gates"`
}

type Target struct {
	Component string `json:"component"`
	Version   string `json:"version"`
}

type ResearchEntry struct {
	Title       string   `json:"title"`
	Publisher   string   `json:"publisher"`
	URL         string   `json:"url"`
	ConsultedAt string   `json:"consultedAt"`
	Facts       []string `json:"facts"`
}

// Decode parses a serialized architecture without replacing schema validation.
func Decode(data []byte) (Design, error) {
	var design Design
	if err := json.Unmarshal(data, &design); err != nil {
		return Design{}, err
	}
	return design, nil
}

// Build returns the architecture represented by architecture/design.json.
func Build() (Design, error) {
	return Design{}, errors.New("architecture design is not implemented")
}
