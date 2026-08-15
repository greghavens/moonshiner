package architecture

import (
	"encoding/json"
	"errors"
)

// Architecture is the machine-readable deployment and migration design.
type Architecture struct {
	SchemaVersion    string        `json:"schemaVersion"`
	TargetVCFVersion string        `json:"targetVcfVersion"`
	Greenfield       Greenfield    `json:"greenfield"`
	MigrationPlan    MigrationPlan `json:"migrationPlan"`
}

type Greenfield struct {
	SddcSpec                    json.RawMessage `json:"sddcSpec"`
	Sites                       []SiteDesign    `json:"sites"`
	Availability                Availability    `json:"availability"`
	ManagementServicesIPReserve int             `json:"managementServicesIpReserve"`
	InternalServicesCIDR        string          `json:"internalServicesCidr"`
}

type SiteDesign struct {
	Name                   string  `json:"name"`
	Role                   string  `json:"role"`
	DemandVCPU             int     `json:"demandVcpu"`
	VCPUPerCore            int     `json:"vcpuPerCore"`
	DemandMemoryTiB        float64 `json:"demandMemoryTiB"`
	DemandUsableStorageTB  float64 `json:"demandUsableStorageTB"`
	ReservePercent         int     `json:"reservePercent"`
	ManagementHosts        int     `json:"managementHosts"`
	WorkloadHosts          int     `json:"workloadHosts"`
	WorkloadClusters       int     `json:"workloadClusters"`
	CoresPerHost           int     `json:"coresPerHost"`
	MemoryTiBPerHost       float64 `json:"memoryTiBPerHost"`
	UsableStorageTBPerHost float64 `json:"usableStorageTBPerHost"`
	FailureToleranceHosts  int     `json:"failureToleranceHosts"`
}

type Availability struct {
	Topology         string `json:"topology"`
	InterSiteRTTMS   int    `json:"interSiteRttMs"`
	StretchedCluster bool   `json:"stretchedCluster"`
	RecoveryMode     string `json:"recoveryMode"`
	RPOMinutes       int    `json:"rpoMinutes"`
	RTOMinutes       int    `json:"rtoMinutes"`
}

type MigrationPlan struct {
	SchemaVersion    string          `json:"schemaVersion"`
	EstateID         string          `json:"estateId"`
	Strategy         string          `json:"strategy"`
	TargetVCFVersion string          `json:"targetVcfVersion"`
	Steps            []MigrationStep `json:"steps"`
}

type MigrationStep struct {
	Order          int      `json:"order"`
	ComponentID    string   `json:"componentId"`
	Component      string   `json:"component"`
	CurrentVersion string   `json:"currentVersion"`
	Target         string   `json:"target"`
	Action         string   `json:"action"`
	Gates          []string `json:"gates"`
}

// Build returns the complete architecture derived from the protected inventory.
// It must not mutate package state and must be safe for concurrent callers.
func Build(inventoryPath string) (Architecture, error) {
	return Architecture{}, errors.New("architecture not implemented")
}
