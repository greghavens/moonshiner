package architecture

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
)

const SchemaVersion = "vcf-migration-plan/v1"

type Inventory struct {
	EstateID string         `json:"estate_id"`
	Domains  []Domain       `json:"domains"`
	Sources  []SourceSystem `json:"sources"`
}

type Domain struct {
	ID                  string `json:"id"`
	Role                string `json:"role"`
	HostCount           int    `json:"host_count"`
	FailuresToTolerate  int    `json:"failures_to_tolerate"`
	Protected           bool   `json:"protected"`
	AvailableForTargets bool   `json:"available_for_targets"`
}

type SourceSystem struct {
	ID           string         `json:"id"`
	Product      string         `json:"product"`
	Version      string         `json:"version"`
	HostedDomain string         `json:"hosted_domain"`
	Content      map[string]int `json:"content"`
	Load         SourceLoad     `json:"load"`
}

type SourceLoad struct {
	ManagedObjects    int `json:"managed_objects,omitempty"`
	ConcurrentJobs    int `json:"concurrent_jobs,omitempty"`
	DailyIngestGiB    int `json:"daily_ingest_gib,omitempty"`
	RetentionDays     int `json:"retention_days,omitempty"`
	ActiveDeployments int `json:"active_deployments,omitempty"`
}

type CompatibilitySnapshot struct {
	SnapshotID        string             `json:"snapshot_id"`
	AsOf              string             `json:"as_of"`
	TargetDomainID    string             `json:"target_domain_id"`
	Topology          TopologyRule       `json:"topology"`
	PlacementProfiles []PlacementProfile `json:"placement_profiles"`
	MigrationRules    []MigrationRule    `json:"migration_rules"`
	Dependencies      []Dependency       `json:"dependencies"`
	Foundation        FoundationRules    `json:"foundation"`
	CutoverGates      []string           `json:"cutover_gates"`
	RetirementGates   []string           `json:"retirement_gates"`
}

type TopologyRule struct {
	MinimumHostsRule       string `json:"minimum_hosts_rule"`
	RequireAntiAffinity    bool   `json:"require_anti_affinity"`
	ManagementDomainImpact string `json:"management_domain_impact"`
}

type PlacementProfile struct {
	Component        string `json:"component"`
	Version          string `json:"version"`
	Nodes            int    `json:"nodes"`
	VCPUPerNode      int    `json:"vcpu_per_node"`
	MemoryGiBPerNode int    `json:"memory_gib_per_node"`
	DiskGiBPerNode   int    `json:"disk_gib_per_node"`
	Purpose          string `json:"purpose"`
}

type MigrationRule struct {
	SourceProduct      string        `json:"source_product"`
	SourceVersion      string        `json:"source_version"`
	TargetComponent    string        `json:"target_component"`
	TargetVersion      string        `json:"target_version"`
	Method             string        `json:"method"`
	ProhibitedMethods  []string      `json:"prohibited_methods"`
	SourceEOGS         string        `json:"source_eogs"`
	SupportBoundary    string        `json:"support_boundary"`
	ContentRules       []ContentRule `json:"content_rules"`
	RequiredEntryGates []string      `json:"required_entry_gates"`
	RequiredExitGates  []string      `json:"required_exit_gates"`
}

type ContentRule struct {
	Category      string `json:"category"`
	Disposition   string `json:"disposition"`
	CarryMethod   string `json:"carry_method,omitempty"`
	MaxCarry      int    `json:"max_carry,omitempty"`
	AbandonReason string `json:"abandon_reason,omitempty"`
}

type Dependency struct {
	BeforeSourceID string `json:"before_source_id"`
	AfterSourceID  string `json:"after_source_id"`
	Reason         string `json:"reason"`
}

type FoundationRules struct {
	ManagementEntryGates []string `json:"management_entry_gates"`
	ManagementExitGates  []string `json:"management_exit_gates"`
	PlacementEntryGates  []string `json:"placement_entry_gates"`
	PlacementExitGates   []string `json:"placement_exit_gates"`
}

type Plan struct {
	SchemaVersion string              `json:"schema_version"`
	EstateID      string              `json:"estate_id"`
	SnapshotID    string              `json:"snapshot_id"`
	Research      []ResearchReference `json:"research"`
	Placement     PlacementPlan       `json:"placement"`
	Steps         []Step              `json:"steps"`
}

type ResearchReference struct {
	Title      string   `json:"title"`
	URL        string   `json:"url"`
	AccessedAt string   `json:"accessed_at"`
	Claims     []string `json:"claims"`
}

type PlacementPlan struct {
	DomainID               string               `json:"domain_id"`
	HostCount              int                  `json:"host_count"`
	FailuresToTolerate     int                  `json:"failures_to_tolerate"`
	ManagementDomainImpact string               `json:"management_domain_impact"`
	PreservedDomains       []string             `json:"preserved_domains"`
	Components             []ComponentPlacement `json:"components"`
}

type ComponentPlacement struct {
	Component        string `json:"component"`
	Version          string `json:"version"`
	Nodes            int    `json:"nodes"`
	VCPUPerNode      int    `json:"vcpu_per_node"`
	MemoryGiBPerNode int    `json:"memory_gib_per_node"`
	DiskGiBPerNode   int    `json:"disk_gib_per_node"`
	AntiAffinity     bool   `json:"anti_affinity"`
	Purpose          string `json:"purpose"`
}

type Step struct {
	Order           int            `json:"order"`
	ID              string         `json:"id"`
	Kind            string         `json:"kind"`
	SourceID        string         `json:"source_id,omitempty"`
	TargetComponent string         `json:"target_component,omitempty"`
	Actions         []string       `json:"actions"`
	EntryGates      []Gate         `json:"entry_gates"`
	ExitGates       []Gate         `json:"exit_gates"`
	Migration       *MigrationPlan `json:"migration,omitempty"`
}

type Gate struct {
	Name     string `json:"name"`
	Evidence string `json:"evidence"`
}

type MigrationPlan struct {
	SourceProduct   string            `json:"source_product"`
	SourceVersion   string            `json:"source_version"`
	TargetComponent string            `json:"target_component"`
	TargetVersion   string            `json:"target_version"`
	Method          string            `json:"method"`
	SourceEOGS      string            `json:"source_eogs"`
	SupportBoundary string            `json:"support_boundary"`
	Content         []ContentDecision `json:"content"`
	Rollback        string            `json:"rollback"`
}

type ContentDecision struct {
	Category        string `json:"category"`
	SourceQuantity  int    `json:"source_quantity"`
	CarryQuantity   int    `json:"carry_quantity"`
	AbandonQuantity int    `json:"abandon_quantity"`
	CarryMethod     string `json:"carry_method,omitempty"`
	AbandonReason   string `json:"abandon_reason,omitempty"`
}

func DecodeJSONFile(path string, dst any) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	dec := json.NewDecoder(f)
	dec.DisallowUnknownFields()
	if err := dec.Decode(dst); err != nil {
		return err
	}
	if err := dec.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return fmt.Errorf("%s contains trailing JSON", path)
	}
	return nil
}

func WritePlan(path string, plan Plan) error {
	b, err := json.MarshalIndent(plan, "", "  ")
	if err != nil {
		return err
	}
	b = append(b, '\n')
	return os.WriteFile(path, b, 0o644)
}

// BuildPlan returns the deterministic architecture derived from the immutable
// inventory and compatibility snapshot. Research is passed through unchanged.
func BuildPlan(inv Inventory, snap CompatibilitySnapshot, research []ResearchReference) (Plan, error) {
	return Plan{}, errors.New("BuildPlan not implemented")
}

// ValidatePlan verifies the architecture without network access.
func ValidatePlan(plan Plan, inv Inventory, snap CompatibilitySnapshot) error {
	return errors.New("ValidatePlan not implemented")
}
