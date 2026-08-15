package vcfdesign

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
)

// Artifact is the machine-readable architecture emitted by this package.
type Artifact struct {
	SchemaVersion string        `json:"schemaVersion"`
	Greenfield    Greenfield    `json:"greenfield"`
	MigrationPlan MigrationPlan `json:"migrationPlan"`
}

type Greenfield struct {
	TargetRelease      string             `json:"targetRelease"`
	SddcSpec           map[string]any     `json:"sddcSpec"`
	Topology           Topology           `json:"topology"`
	Capacity           CapacityDecision   `json:"capacity"`
	ManagementServices ManagementServices `json:"managementServices"`
	BillOfMaterials    map[string]string  `json:"billOfMaterials"`
}

type Topology struct {
	Sites                     []SitePlacement           `json:"sites"`
	HostProfile               HostProfile               `json:"hostProfile"`
	StretchedManagementDomain StretchedManagementDomain `json:"stretchedManagementDomain"`
}

type SitePlacement struct {
	ID        string   `json:"id"`
	Role      string   `json:"role"`
	Hostnames []string `json:"hostnames"`
}

type HostProfile struct {
	CoresPerHost         int     `json:"coresPerHost"`
	MemoryGiBPerHost     int     `json:"memoryGiBPerHost"`
	RawStorageTiBPerHost float64 `json:"rawStorageTiBPerHost"`
}

type StretchedManagementDomain struct {
	Enabled    bool       `json:"enabled"`
	RPOSeconds int        `json:"rpoSeconds"`
	VsanPolicy VsanPolicy `json:"vsanPolicy"`
	Witness    Witness    `json:"witness"`
}

type VsanPolicy struct {
	FailuresToTolerate    int    `json:"failuresToTolerate"`
	SiteDisasterTolerance string `json:"siteDisasterTolerance"`
	PreferredSite         string `json:"preferredSite"`
}

type Witness struct {
	Hostname               string `json:"hostname"`
	Site                   string `json:"site"`
	FailureDomain          string `json:"failureDomain"`
	Kind                   string `json:"kind"`
	RunsOnManagementDomain bool   `json:"runsOnManagementDomain"`
}

type CapacityDecision struct {
	ReservePercent            int      `json:"reservePercent"`
	SiteFailureSurvivingHosts int      `json:"siteFailureSurvivingHosts"`
	UsableAfterReserve        Capacity `json:"usableAfterReserve"`
	Required                  Capacity `json:"required"`
}

type Capacity struct {
	CPUCores   float64 `json:"cpuCores"`
	MemoryGiB  float64 `json:"memoryGiB"`
	StorageTiB float64 `json:"storageTiB"`
}

type ManagementServices struct {
	MinimumAddresses int    `json:"minimumAddresses"`
	PoolStart        string `json:"poolStart"`
	PoolEnd          string `json:"poolEnd"`
	NetworkType      string `json:"networkType"`
	InternalCIDR     string `json:"internalCidr"`
}

type MigrationPlan struct {
	SchemaVersion string          `json:"schemaVersion"`
	EstateID      string          `json:"estateId"`
	TargetRelease string          `json:"targetRelease"`
	Steps         []MigrationStep `json:"steps"`
}

type MigrationStep struct {
	Order          int      `json:"order"`
	Component      string   `json:"component"`
	CurrentVersion string   `json:"currentVersion"`
	Target         string   `json:"target"`
	Action         string   `json:"action"`
	Gates          []string `json:"gates"`
}

// Build reads the immutable scenario inputs and returns the complete architecture.
func Build(requirementsPath, estatePath, compatibilityPath string) (Artifact, error) {
	return Artifact{}, errors.New("architecture design not implemented")
}

// WriteArtifact writes an Artifact as stable, indented JSON.
func WriteArtifact(path string, artifact Artifact) error {
	b, err := json.MarshalIndent(artifact, "", "  ")
	if err != nil {
		return err
	}
	b = append(b, '\n')
	dir := filepath.Dir(path)
	if dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return err
		}
	}
	return os.WriteFile(path, b, 0o644)
}
