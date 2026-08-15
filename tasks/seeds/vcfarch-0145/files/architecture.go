// Package architecture publishes the machine-readable VCF architecture.
package architecture

import (
	"encoding/json"
	"errors"
)

// Architecture contains the greenfield and existing-estate views of the site.
type Architecture struct {
	Greenfield     GreenfieldDesign `json:"greenfield"`
	ExistingEstate MigrationPlan    `json:"existingEstate"`
}

// GreenfieldDesign couples topology intent to the VCF Installer SddcSpec.
type GreenfieldDesign struct {
	Topology Topology        `json:"topology"`
	SddcSpec json.RawMessage `json:"sddcSpec"`
}

// Topology makes the consolidation and minimum-host decisions explicit.
type Topology struct {
	Sites             int    `json:"sites"`
	AvailabilityZones int    `json:"availabilityZones"`
	ClusterModel      string `json:"clusterModel"`
	ManagementDomain  string `json:"managementDomain"`
	HostCount         int    `json:"hostCount"`
	Storage           string `json:"storage"`
}

// MigrationPlan is the ordered existing-estate architecture.
type MigrationPlan struct {
	PlanID        string          `json:"planId"`
	InventoryID   string          `json:"inventoryId"`
	SourceVersion string          `json:"sourceVersion"`
	TargetVersion string          `json:"targetVersion"`
	SelectedPath  []string        `json:"selectedPath"`
	AvoidedHops   []AvoidedHop    `json:"avoidedHops"`
	Steps         []MigrationStep `json:"steps"`
}

// AvoidedHop records a pinned transition that this plan routes around.
type AvoidedHop struct {
	Component string `json:"component"`
	From      string `json:"from"`
	To        string `json:"to"`
	Gate      string `json:"gate"`
}

// MigrationStep names one inventoried component and its gates.
type MigrationStep struct {
	Order          int      `json:"order"`
	Component      string   `json:"component"`
	CurrentVersion string   `json:"currentVersion"`
	TargetVersion  string   `json:"targetVersion"`
	GatedBy        []string `json:"gatedBy"`
}

var artifactJSON []byte

// Load returns the embedded architecture artifact.
func Load() (Architecture, error) {
	return Architecture{}, errors.New("architecture is not implemented")
}
