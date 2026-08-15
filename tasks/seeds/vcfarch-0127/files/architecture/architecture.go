// Package architecture builds the deterministic VCF target architecture.
package architecture

import (
	"encoding/json"
	"fmt"
)

// Inventory is the immutable brownfield estate input.
type Inventory struct {
	SchemaVersion string      `json:"schemaVersion"`
	EstateID      string      `json:"estateId"`
	Sites         []string    `json:"sites"`
	Scale         EstateScale `json:"scale"`
	Components    []Component `json:"components"`
}

type EstateScale struct {
	VirtualMachines             int `json:"virtualMachines"`
	ManagedHosts                int `json:"managedHosts"`
	LogIngestGiBPerDay          int `json:"logIngestGiBPerDay"`
	AutomationConcurrentDeploys int `json:"automationConcurrentDeployments"`
}

type Component struct {
	ID        string `json:"id"`
	Type      string `json:"type"`
	Version   string `json:"version"`
	Site      string `json:"site"`
	Scope     string `json:"scope"`
	Instances int    `json:"instances,omitempty"`
}

// Snapshot is the pinned, deterministic compatibility and architecture authority.
type Snapshot struct {
	SchemaVersion     string             `json:"schemaVersion"`
	CapturedOn        string             `json:"capturedOn"`
	TargetRelease     string             `json:"targetRelease"`
	Fleet             FleetRule          `json:"fleet"`
	Greenfield        GreenfieldRule     `json:"greenfield"`
	Domains           []DomainRule       `json:"domains"`
	ScopeTargets      map[string]string  `json:"scopeTargets"`
	ServiceSizing     []ServicePlacement `json:"serviceSizing"`
	PlannedComponents []PlannedComponent `json:"plannedComponents"`
	Paths             []PathRule         `json:"paths"`
}

type FleetRule struct {
	Name             string `json:"name"`
	PrimaryInstance  string `json:"primaryInstance"`
	ManagementDomain string `json:"managementDomain"`
}

type SizedServiceRule struct {
	DeploymentModel string `json:"deploymentModel"`
	NodeCount       int    `json:"nodeCount"`
	Size            string `json:"size"`
}

type GreenfieldRule struct {
	SddcID               string           `json:"sddcId"`
	WorkflowType         string           `json:"workflowType"`
	HostCount            int              `json:"hostCount"`
	RequiredSections     []string         `json:"requiredSections"`
	RequiredNetworkTypes []string         `json:"requiredNetworkTypes"`
	Operations           SizedServiceRule `json:"operations"`
	Automation           SizedServiceRule `json:"automation"`
}

type DomainRule struct {
	ID       string `json:"id"`
	Kind     string `json:"kind"`
	Site     string `json:"site"`
	Instance string `json:"instance"`
}

type ServicePlacement struct {
	Service         string `json:"service"`
	ComponentID     string `json:"componentId"`
	Domain          string `json:"domain"`
	DeploymentModel string `json:"deploymentModel"`
	NodeCount       int    `json:"nodeCount"`
	Size            string `json:"size"`
}

type PlannedComponent struct {
	ID            string   `json:"id"`
	Type          string   `json:"type"`
	FromVersion   string   `json:"fromVersion"`
	ToVersion     string   `json:"toVersion"`
	Scope         string   `json:"scope"`
	Action        string   `json:"action"`
	Phase         int      `json:"phase"`
	RequiredGates []string `json:"requiredGates"`
}

type PathRule struct {
	Type          string   `json:"type"`
	From          string   `json:"from"`
	To            string   `json:"to"`
	Scope         string   `json:"scope"`
	Action        string   `json:"action"`
	Phase         int      `json:"phase"`
	RequiredGates []string `json:"requiredGates"`
}

type MigrationPlan struct {
	SchemaVersion string             `json:"schemaVersion"`
	TargetFleet   TargetFleet        `json:"targetFleet"`
	Domains       []Domain           `json:"domains"`
	Services      []ServicePlacement `json:"services"`
	Steps         []MigrationStep    `json:"steps"`
}

type TargetFleet struct {
	Name             string `json:"name"`
	Release          string `json:"release"`
	PrimaryInstance  string `json:"primaryInstance"`
	ManagementDomain string `json:"managementDomain"`
}

type Domain struct {
	ID           string   `json:"id"`
	Kind         string   `json:"kind"`
	Site         string   `json:"site"`
	ComponentIDs []string `json:"componentIds"`
}

type MigrationStep struct {
	Sequence      int        `json:"sequence"`
	ComponentID   string     `json:"componentId"`
	ComponentType string     `json:"componentType"`
	FromVersion   string     `json:"fromVersion"`
	ToVersion     string     `json:"toVersion"`
	Action        string     `json:"action"`
	Target        StepTarget `json:"target"`
	Gates         []StepGate `json:"gates"`
}

type StepTarget struct {
	Fleet    string `json:"fleet"`
	Instance string `json:"instance"`
	Domain   string `json:"domain"`
}

type StepGate struct {
	ID        string `json:"id"`
	Condition string `json:"condition"`
}

// DecodeInputs parses the two protected inputs without reading any live research.
func DecodeInputs(inventoryJSON, snapshotJSON []byte) (Inventory, Snapshot, error) {
	var inventory Inventory
	if err := json.Unmarshal(inventoryJSON, &inventory); err != nil {
		return Inventory{}, Snapshot{}, fmt.Errorf("decode inventory: %w", err)
	}
	var snapshot Snapshot
	if err := json.Unmarshal(snapshotJSON, &snapshot); err != nil {
		return Inventory{}, Snapshot{}, fmt.Errorf("decode compatibility snapshot: %w", err)
	}
	return inventory, snapshot, nil
}

// GreenfieldSddc returns a JSON-compatible SddcSpec for a new management domain.
func GreenfieldSddc(snapshot Snapshot) (map[string]any, error) {
	return nil, fmt.Errorf("greenfield architecture not implemented")
}

// BrownfieldPlan maps every inventory component into the single target fleet.
func BrownfieldPlan(inventory Inventory, snapshot Snapshot) (MigrationPlan, error) {
	return MigrationPlan{}, fmt.Errorf("brownfield architecture not implemented")
}
