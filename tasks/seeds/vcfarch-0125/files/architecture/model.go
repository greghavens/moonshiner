package architecture

import (
	"encoding/json"
	"fmt"
	"io"
)

// Inventory is the complete planning input. Component IDs are stable estate
// identifiers and must be preserved in a migration plan.
type Inventory struct {
	InventoryVersion string          `json:"inventoryVersion"`
	EstateName       string          `json:"estateName"`
	FleetTarget      string          `json:"fleetTarget"`
	Components       []Component     `json:"components"`
	Greenfield       GreenfieldInput `json:"greenfield"`
}

type Component struct {
	ID           string `json:"id"`
	Type         string `json:"type"`
	Product      string `json:"product"`
	Version      string `json:"version"`
	Architecture string `json:"architecture,omitempty"`
}

type GreenfieldInput struct {
	SDDCID                      string           `json:"sddcId"`
	Version                     string           `json:"version"`
	WorkflowType                string           `json:"workflowType"`
	Optimization                string           `json:"optimization"`
	AllowedStorageArchitectures []string         `json:"allowedStorageArchitectures"`
	ReserveHosts                int              `json:"reserveHosts"`
	PerformanceProfile          string           `json:"performanceProfile"`
	Hostnames                   []string         `json:"hostnames"`
	DNSSpec                     map[string]any   `json:"dnsSpec"`
	VcenterSpec                 map[string]any   `json:"vcenterSpec"`
	NetworkSpecs                []map[string]any `json:"networkSpecs"`
	DvsSpec                     map[string]any   `json:"dvsSpec"`
	NsxtSpec                    map[string]any   `json:"nsxtSpec"`
}

type CompatibilitySnapshot struct {
	SnapshotVersion string            `json:"snapshotVersion"`
	PinnedOn        string            `json:"pinnedOn"`
	FleetTarget     string            `json:"fleetTarget"`
	StorageOptions  []StorageOption   `json:"storageOptions"`
	MigrationRules  []MigrationRule   `json:"migrationRules"`
	GateCatalog     map[string]string `json:"gateCatalog"`
}

type StorageOption struct {
	Architecture        string         `json:"architecture"`
	Policy              string         `json:"policy"`
	FailuresToTolerate  int            `json:"failuresToTolerate"`
	PolicyMinimumHosts  int            `json:"policyMinimumHosts"`
	PreferenceRank      int            `json:"preferenceRank"`
	UplinksPerHost      int            `json:"uplinksPerHost"`
	UplinkGbpsByProfile map[string]int `json:"uplinkGbpsByProfile"`
	VsanMTU             int            `json:"vsanMtu"`
	ESAEnabled          bool           `json:"esaEnabled"`
	HardwareRequirement string         `json:"hardwareRequirement"`
}

type MigrationRule struct {
	ComponentType       string   `json:"componentType"`
	FromVersion         string   `json:"fromVersion"`
	FromArchitecture    string   `json:"fromArchitecture,omitempty"`
	TargetProduct       string   `json:"targetProduct"`
	TargetVersion       string   `json:"targetVersion"`
	TargetArchitecture  string   `json:"targetArchitecture,omitempty"`
	Action              string   `json:"action"`
	UpgradePath         []string `json:"upgradePath"`
	Gates               []string `json:"gates"`
	AfterComponentTypes []string `json:"afterComponentTypes"`
	Sequence            int      `json:"sequence"`
}

type Architecture struct {
	SchemaVersion string           `json:"schemaVersion"`
	FleetTarget   string           `json:"fleetTarget"`
	Greenfield    GreenfieldDesign `json:"greenfield"`
	Migration     MigrationPlan    `json:"migration"`
}

type GreenfieldDesign struct {
	StorageDecision StorageDecision `json:"storageDecision"`
	SDDCSpec        map[string]any  `json:"sddcSpec"`
}

type StorageDecision struct {
	SelectedArchitecture string               `json:"selectedArchitecture"`
	SelectionCriterion   string               `json:"selectionCriterion"`
	HostCount            int                  `json:"hostCount"`
	Policy               string               `json:"policy"`
	Network              StorageNetwork       `json:"network"`
	Alternatives         []StorageAlternative `json:"alternatives"`
}

type StorageNetwork struct {
	UplinksPerHost int `json:"uplinksPerHost"`
	UplinkGbps     int `json:"uplinkGbps"`
	VsanMTU        int `json:"vsanMtu"`
}

type StorageAlternative struct {
	Architecture        string `json:"architecture"`
	Policy              string `json:"policy"`
	HostCount           int    `json:"hostCount"`
	UplinksPerHost      int    `json:"uplinksPerHost"`
	UplinkGbps          int    `json:"uplinkGbps"`
	VsanMTU             int    `json:"vsanMtu"`
	ESAEnabled          bool   `json:"esaEnabled"`
	HardwareRequirement string `json:"hardwareRequirement"`
}

type MigrationPlan struct {
	Estate       string          `json:"estate"`
	OrderedSteps []MigrationStep `json:"orderedSteps"`
}

type MigrationStep struct {
	Order       int              `json:"order"`
	Component   PlannedComponent `json:"component"`
	Target      PlannedTarget    `json:"target"`
	Action      string           `json:"action"`
	UpgradePath []string         `json:"upgradePath"`
	Gates       []string         `json:"gates"`
}

type PlannedComponent struct {
	ID           string `json:"id"`
	Type         string `json:"type"`
	Product      string `json:"product"`
	Version      string `json:"version"`
	Architecture string `json:"architecture,omitempty"`
}

type PlannedTarget struct {
	Product      string `json:"product"`
	Version      string `json:"version"`
	Architecture string `json:"architecture,omitempty"`
}

func LoadInventory(r io.Reader) (Inventory, error) {
	var v Inventory
	dec := json.NewDecoder(r)
	if err := dec.Decode(&v); err != nil {
		return Inventory{}, fmt.Errorf("decode inventory: %w", err)
	}
	return v, nil
}

func LoadCompatibilitySnapshot(r io.Reader) (CompatibilitySnapshot, error) {
	var v CompatibilitySnapshot
	dec := json.NewDecoder(r)
	if err := dec.Decode(&v); err != nil {
		return CompatibilitySnapshot{}, fmt.Errorf("decode compatibility snapshot: %w", err)
	}
	return v, nil
}
