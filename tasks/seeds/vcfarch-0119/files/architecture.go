package vcfarch

import (
	"errors"
)

// Inventory is the separately managed estate and the physical placement input.
type Inventory struct {
	EstateID         string            `json:"estateId"`
	TargetVCFVersion string            `json:"targetVcfVersion"`
	Sites            []Site            `json:"sites"`
	Components       []EstateComponent `json:"components"`
}

type Site struct {
	ID               string   `json:"id"`
	Role             string   `json:"role"`
	FailureDomain    string   `json:"failureDomain"`
	ManagementHosts  []string `json:"managementHosts,omitempty"`
	WitnessAppliance string   `json:"witnessAppliance,omitempty"`
}

type EstateComponent struct {
	ID      string `json:"id"`
	Type    string `json:"type"`
	Site    string `json:"site"`
	Version string `json:"version"`
}

// CompatibilitySnapshot is the pinned, deterministic compatibility authority.
type CompatibilitySnapshot struct {
	SnapshotVersion  string        `json:"snapshotVersion"`
	TargetVCFVersion string        `json:"targetVcfVersion"`
	ProductRules     []ProductRule `json:"productRules"`
	Gates            []GateRule    `json:"gates"`
}

type ProductRule struct {
	ComponentType       string   `json:"componentType"`
	AllowedFromVersions []string `json:"allowedFromVersions"`
	TargetProduct       string   `json:"targetProduct"`
	TargetVersion       string   `json:"targetVersion"`
	AllowedActions      []string `json:"allowedActions"`
	RequiredGateIDs     []string `json:"requiredGateIds"`
}

type GateRule struct {
	ID               string   `json:"id"`
	AppliesToTypes   []string `json:"appliesToTypes"`
	PredecessorTypes []string `json:"predecessorTypes"`
	Scope            string   `json:"scope"`
	Requirement      string   `json:"requirement"`
}

type Architecture struct {
	GreenfieldSddcSpec map[string]any `json:"greenfieldSddcSpec"`
	Topology           Topology       `json:"topology"`
	MigrationPlan      MigrationPlan  `json:"migrationPlan"`
}

type Topology struct {
	ManagementDomain ManagementDomain `json:"managementDomain"`
}

type ManagementDomain struct {
	Name      string     `json:"name"`
	Stretched bool       `json:"stretched"`
	DataSites []DataSite `json:"dataSites"`
	Witness   Witness    `json:"witness"`
}

type DataSite struct {
	SiteID        string   `json:"siteId"`
	FailureDomain string   `json:"failureDomain"`
	Hosts         []string `json:"hosts"`
}

type Witness struct {
	SiteID                   string `json:"siteId"`
	FailureDomain            string `json:"failureDomain"`
	Appliance                string `json:"appliance"`
	IndependentFailureDomain bool   `json:"independentFailureDomain"`
}

type MigrationPlan struct {
	EstateID         string          `json:"estateId"`
	TargetVCFVersion string          `json:"targetVcfVersion"`
	Steps            []MigrationStep `json:"steps"`
}

type MigrationStep struct {
	Order         int      `json:"order"`
	ComponentID   string   `json:"componentId"`
	ComponentType string   `json:"componentType"`
	Site          string   `json:"site"`
	FromVersion   string   `json:"fromVersion"`
	TargetProduct string   `json:"targetProduct"`
	TargetVersion string   `json:"targetVersion"`
	Action        string   `json:"action"`
	Gates         []string `json:"gates"`
}

// BuildArchitecture returns the complete greenfield and brownfield architecture.
func BuildArchitecture(_ Inventory, _ CompatibilitySnapshot) (Architecture, error) {
	return Architecture{}, errors.New("BuildArchitecture is not implemented")
}
