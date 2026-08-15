package architecture

import (
	"encoding/json"
	"fmt"
	"os"
)

const (
	InventoryPath = "fixtures/estate.json"
	SnapshotPath  = "fixtures/compatibility-snapshot.json"
	ArtifactPath  = "migration-plan.json"
)

type Inventory struct {
	SchemaVersion   string          `json:"schemaVersion"`
	EstateID        string          `json:"estateId"`
	SourceRelease   string          `json:"sourceRelease"`
	TargetRelease   string          `json:"targetRelease"`
	Domain          Domain          `json:"domain"`
	Components      []InventoryItem `json:"components"`
	InstallerInputs InstallerInputs `json:"installerInputs"`
}

type Domain struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	Type string `json:"type"`
}

type InventoryItem struct {
	ID      string `json:"id"`
	Name    string `json:"name"`
	Version string `json:"version"`
	FQDN    string `json:"fqdn,omitempty"`
}

type InstallerInputs struct {
	SddcID              string   `json:"sddcId"`
	VcenterHostname     string   `json:"vcenterHostname"`
	RootVcenterPassword string   `json:"rootVcenterPassword"`
	Subdomain           string   `json:"subdomain"`
	Nameservers         []string `json:"nameservers"`
	ManagementVLAN      int      `json:"managementVlan"`
}

type CompatibilitySnapshot struct {
	SchemaVersion string              `json:"schemaVersion"`
	SnapshotID    string              `json:"snapshotId"`
	SourceRelease string              `json:"sourceRelease"`
	TargetRelease string              `json:"targetRelease"`
	Components    []ComponentContract `json:"components"`
	Gates         []GateContract      `json:"gates"`
}

type ComponentContract struct {
	ID                 string       `json:"id"`
	CurrentVersion     string       `json:"currentVersion"`
	TargetVersion      string       `json:"targetVersion"`
	FinalGate          string       `json:"finalGate"`
	AllowedTransitions []Transition `json:"allowedTransitions"`
	BlockedTransitions []Transition `json:"blockedTransitions,omitempty"`
}

type Transition struct {
	From      string `json:"from"`
	To        string `json:"to"`
	Operation string `json:"operation"`
}

type GateContract struct {
	ID               string   `json:"id"`
	Action           string   `json:"action"`
	ComponentID      string   `json:"componentId,omitempty"`
	Requires         []string `json:"requires"`
	FromVersion      string   `json:"fromVersion,omitempty"`
	ToVersion        string   `json:"toVersion,omitempty"`
	Produces         string   `json:"produces"`
	MinimumRationale int      `json:"minimumRationale"`
}

type Plan struct {
	SchemaVersion  string          `json:"schemaVersion"`
	EstateID       string          `json:"estateId"`
	SourceRelease  string          `json:"sourceRelease"`
	TargetRelease  string          `json:"targetRelease"`
	SnapshotID     string          `json:"snapshotId"`
	TargetSddcSpec json.RawMessage `json:"targetSddcSpec"`
	Components     []ComponentPlan `json:"components"`
	Steps          []Step          `json:"steps"`
}

type ComponentPlan struct {
	ID             string   `json:"id"`
	Name           string   `json:"name"`
	CurrentVersion string   `json:"currentVersion"`
	TargetVersion  string   `json:"targetVersion"`
	GatedBy        []string `json:"gatedBy"`
}

type Step struct {
	Order       int      `json:"order"`
	ID          string   `json:"id"`
	Action      string   `json:"action"`
	ComponentID string   `json:"componentId,omitempty"`
	FromVersion string   `json:"fromVersion,omitempty"`
	ToVersion   string   `json:"toVersion,omitempty"`
	Requires    []string `json:"requires"`
	Produces    string   `json:"produces"`
	Rationale   string   `json:"rationale"`
}

func ReadJSON(path string, dst any) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("read %s: %w", path, err)
	}
	if err := json.Unmarshal(raw, dst); err != nil {
		return fmt.Errorf("decode %s: %w", path, err)
	}
	return nil
}

func WritePlan(path string, plan Plan) error {
	raw, err := json.MarshalIndent(plan, "", "  ")
	if err != nil {
		return fmt.Errorf("encode plan: %w", err)
	}
	raw = append(raw, '\n')
	if err := os.WriteFile(path, raw, 0o644); err != nil {
		return fmt.Errorf("write %s: %w", path, err)
	}
	return nil
}
