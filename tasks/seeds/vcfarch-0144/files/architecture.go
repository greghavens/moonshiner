package vcfarch

import (
	"encoding/json"
)

// Inventory is the existing estate supplied to Build.
type Inventory struct {
	EstateID       string               `json:"estateId"`
	CurrentRelease string               `json:"currentRelease"`
	Sites          []InventorySite      `json:"sites"`
	Networks       []InventoryNetwork   `json:"networks"`
	Components     []InventoryComponent `json:"components"`
	Greenfield     GreenfieldInputs     `json:"greenfield"`
}

type GreenfieldInputs struct {
	SddcID                  string   `json:"sddcId"`
	Subdomain               string   `json:"subdomain"`
	NameServers             []string `json:"nameServers"`
	NTPServers              []string `json:"ntpServers"`
	VcenterHostname         string   `json:"vcenterHostname"`
	SddcManagerHostname     string   `json:"sddcManagerHostname"`
	NSXManagerHostnames     []string `json:"nsxManagerHostnames"`
	NSXVIP                  string   `json:"nsxVip"`
	ClusterName             string   `json:"clusterName"`
	DatacenterName          string   `json:"datacenterName"`
	DVSName                 string   `json:"dvsName"`
	DatastoreName           string   `json:"datastoreName"`
	VcenterRootPasswordRef  string   `json:"vcenterRootPasswordRef"`
	SddcManagerPasswordRef  string   `json:"sddcManagerPasswordRef"`
	SddcManagerLocalUserRef string   `json:"sddcManagerLocalUserRef"`
	NSXManagerPasswordRef   string   `json:"nsxManagerPasswordRef"`
	ManagementPoolName      string   `json:"managementPoolName"`
	ExpectedManagementHosts int      `json:"expectedManagementHosts"`
}

type InventorySite struct {
	ID            string `json:"id"`
	FailureDomain string `json:"failureDomain"`
	Role          string `json:"role"`
}

type InventoryNetwork struct {
	Type    string `json:"type"`
	VLAN    int    `json:"vlan"`
	CIDR    string `json:"cidr"`
	Gateway string `json:"gateway"`
	MTU     int    `json:"mtu"`
}

type InventoryComponent struct {
	ID      string   `json:"id"`
	Name    string   `json:"name"`
	Type    string   `json:"type"`
	Version string   `json:"version"`
	Site    string   `json:"site"`
	Members []string `json:"members,omitempty"`
}

// CompatibilitySnapshot is the immutable grading authority. Live research is
// required for design provenance, but Build must use these pinned decisions.
type CompatibilitySnapshot struct {
	SnapshotID     string                 `json:"snapshotId"`
	TargetRelease  string                 `json:"targetRelease"`
	TargetVersions map[string]string      `json:"targetVersions"`
	Transitions    []TransitionRule       `json:"transitions"`
	Dependencies   []DependencyRule       `json:"dependencies"`
	Restrictions   []CompatibilityBlocker `json:"restrictions"`
}

type TransitionRule struct {
	ComponentType string   `json:"componentType"`
	FromVersion   string   `json:"fromVersion"`
	ToVersion     string   `json:"toVersion"`
	AllowedAction string   `json:"allowedAction"`
	Forbidden     []string `json:"forbiddenActions"`
	RequiredGates []string `json:"requiredGates"`
}

type DependencyRule struct {
	ComponentID string   `json:"componentId"`
	DependsOn   []string `json:"dependsOn"`
}

type CompatibilityBlocker struct {
	ID               string   `json:"id"`
	AffectedTypes    []string `json:"affectedTypes"`
	AffectedVersions []string `json:"affectedVersions"`
	ForbiddenAction  string   `json:"forbiddenAction"`
	Route            string   `json:"route"`
}

// Architecture is the serializable architecture artifact returned by Build.
type Architecture struct {
	Greenfield    GreenfieldDesign `json:"greenfield"`
	MigrationPlan MigrationPlan    `json:"migrationPlan"`
}

type GreenfieldDesign struct {
	SddcSpec json.RawMessage    `json:"sddcSpec"`
	Topology ManagementTopology `json:"topology"`
}

type ManagementTopology struct {
	Stretched bool                `json:"stretched"`
	DataSites []DataSitePlacement `json:"dataSites"`
	Witness   WitnessPlacement    `json:"witness"`
}

type DataSitePlacement struct {
	SiteID        string   `json:"siteId"`
	FailureDomain string   `json:"failureDomain"`
	Hosts         []string `json:"hosts"`
}

type WitnessPlacement struct {
	ComponentID            string `json:"componentId"`
	SiteID                 string `json:"siteId"`
	FailureDomain          string `json:"failureDomain"`
	Hostname               string `json:"hostname"`
	Version                string `json:"version"`
	PlacementType          string `json:"placementType"`
	Dedicated              bool   `json:"dedicated"`
	RunsOnManagementDomain bool   `json:"runsOnManagementDomain"`
}

type MigrationPlan struct {
	SchemaVersion string          `json:"schemaVersion"`
	EstateID      string          `json:"estateId"`
	TargetRelease string          `json:"targetRelease"`
	Strategy      string          `json:"strategy"`
	Steps         []MigrationStep `json:"steps"`
}

type MigrationStep struct {
	Order         int      `json:"order"`
	ComponentID   string   `json:"componentId"`
	ComponentName string   `json:"componentName"`
	ComponentType string   `json:"componentType"`
	FromVersion   string   `json:"fromVersion"`
	TargetVersion string   `json:"targetVersion"`
	Action        string   `json:"action"`
	Gates         []string `json:"gates"`
	DependsOn     []string `json:"dependsOn"`
}

// Build creates the target architecture from the supplied immutable inputs.
func Build(inventory Inventory, compatibility CompatibilitySnapshot) (Architecture, error) {
	// TODO: implement the architecture.
	return Architecture{}, nil
}
