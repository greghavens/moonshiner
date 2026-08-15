// Package vcfarch designs the two machine-readable VCF architecture artifacts.
package vcfarch

import (
	"errors"
	"io/fs"
)

// Requirements is the protected greenfield design input.
type Requirements struct {
	DesignID         string               `json:"designId"`
	Fleet            string               `json:"fleet"`
	WorkloadDomain   string               `json:"workloadDomain"`
	ManagementDomain string               `json:"managementDomain"`
	TargetVersion    string               `json:"targetVersion"`
	WorkflowType     string               `json:"workflowType"`
	DomainSuffix     string               `json:"domainSuffix"`
	DNS              []string             `json:"dns"`
	NTP              []string             `json:"ntp"`
	Capacity         CapacityRequirement  `json:"capacity"`
	Availability     Availability         `json:"availability"`
	Hosts            []HostCandidate      `json:"hosts"`
	Networks         []NetworkRequirement `json:"networks"`
	Names            ApplianceNames       `json:"names"`
}

type CapacityRequirement struct {
	UsableCores      int     `json:"usableCores"`
	UsableMemoryGiB  int     `json:"usableMemoryGiB"`
	UsableStorageTiB float64 `json:"usableStorageTiB"`
	StorageCopies    int     `json:"storageCopies"`
	FreeSpacePercent int     `json:"freeSpacePercent"`
}

type Availability struct {
	Sites              []string `json:"sites"`
	HostsPerSite       int      `json:"hostsPerSite"`
	SurviveSiteFailure bool     `json:"surviveSiteFailure"`
	FailuresToTolerate int      `json:"failuresToTolerate"`
	WitnessSite        string   `json:"witnessSite"`
	NSXManagerCount    int      `json:"nsxManagerCount"`
}

type HostCandidate struct {
	Hostname      string  `json:"hostname"`
	Site          string  `json:"site"`
	Cores         int     `json:"cores"`
	MemoryGiB     int     `json:"memoryGiB"`
	RawStorageTiB float64 `json:"rawStorageTiB"`
}

type NetworkRequirement struct {
	Type    string `json:"type"`
	VLANID  int    `json:"vlanId"`
	Subnet  string `json:"subnet"`
	Gateway string `json:"gateway"`
	Start   string `json:"start"`
	End     string `json:"end"`
	MTU     int    `json:"mtu"`
	Switch  string `json:"switch"`
}

type ApplianceNames struct {
	Datacenter     string   `json:"datacenter"`
	Cluster        string   `json:"cluster"`
	VCenter        string   `json:"vcenter"`
	SDDCManager    string   `json:"sddcManager"`
	NSXVIP         string   `json:"nsxVip"`
	NSXManagers    []string `json:"nsxManagers"`
	SystemDVS      string   `json:"systemDvs"`
	OverlayDVS     string   `json:"overlayDvs"`
	VSANDatastore  string   `json:"vsanDatastore"`
	NSXTEPPoolName string   `json:"nsxTepPoolName"`
}

// Estate is the brownfield inventory. Every component must occur in the plan.
type Estate struct {
	EstateID         string            `json:"estateId"`
	Fleet            string            `json:"fleet"`
	WorkloadDomain   string            `json:"workloadDomain"`
	ManagementDomain string            `json:"managementDomain"`
	ProtectedSystems []string          `json:"protectedSystems"`
	Components       []EstateComponent `json:"components"`
}

type EstateComponent struct {
	Name    string `json:"name"`
	Version string `json:"version"`
}

// CompatibilitySnapshot is the frozen grading authority, not live research.
type CompatibilitySnapshot struct {
	SchemaVersion string       `json:"schemaVersion"`
	SnapshotID    string       `json:"snapshotId"`
	TargetRelease string       `json:"targetRelease"`
	Transitions   []Transition `json:"transitions"`
}

type Transition struct {
	Component   string   `json:"component"`
	FromVersion string   `json:"fromVersion"`
	Target      Target   `json:"target"`
	Action      string   `json:"action"`
	Order       int      `json:"order"`
	Gates       []string `json:"gates"`
}

type Target struct {
	Component string `json:"component"`
	Version   string `json:"version"`
}

type Inputs struct {
	Requirements  Requirements
	Estate        Estate
	Compatibility CompatibilitySnapshot
}

// SddcSpec is intentionally an object map: the vendored installer OpenAPI is
// the canonical and complete type definition.
type SddcSpec map[string]any

type MigrationPlan struct {
	SchemaVersion string          `json:"schemaVersion"`
	EstateID      string          `json:"estateId"`
	Scope         MigrationScope  `json:"scope"`
	Steps         []MigrationStep `json:"steps"`
}

type MigrationScope struct {
	Fleet                  string `json:"fleet"`
	WorkloadDomain         string `json:"workloadDomain"`
	ManagementDomain       string `json:"managementDomain"`
	ManagementDomainAction string `json:"managementDomainAction"`
}

type MigrationStep struct {
	Order          int      `json:"order"`
	Component      string   `json:"component"`
	CurrentVersion string   `json:"currentVersion"`
	Target         Target   `json:"target"`
	Action         string   `json:"action"`
	Gates          []string `json:"gates"`
}

var ErrNotImplemented = errors.New("vcfarch: not implemented")

func LoadInputs(requirementsPath, estatePath, compatibilityPath string) (Inputs, error) {
	return Inputs{}, ErrNotImplemented
}

func BuildSddcSpec(req Requirements) (SddcSpec, error) {
	return nil, ErrNotImplemented
}

func BuildMigrationPlan(estate Estate, snapshot CompatibilitySnapshot) (MigrationPlan, error) {
	return MigrationPlan{}, ErrNotImplemented
}

// WriteArtifacts writes sddc-spec.json and migration-plan.json.
func WriteArtifacts(dir string, spec SddcSpec, plan MigrationPlan) error {
	return ErrNotImplemented
}

func GenerateFromFiles(requirementsPath, estatePath, compatibilityPath, outputDir string) error {
	return ErrNotImplemented
}

// RenameFS captures the filesystem operations WriteArtifacts needs. It is kept
// private so the package surface remains about architecture, not storage.
type renameFS interface {
	MkdirAll(string, fs.FileMode) error
	WriteFile(string, []byte, fs.FileMode) error
	Rename(string, string) error
	Remove(string) error
}
