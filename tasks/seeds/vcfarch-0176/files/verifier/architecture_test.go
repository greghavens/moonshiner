package verifier

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"strings"
	"testing"

	"example.com/vcfmigration/internal/jsonschema"
	"example.com/vcfmigration/migrationplan"
)

func TestMigrationArchitecture(t *testing.T) {
	root := repositoryRoot(t)

	// The installer schema is intentionally the first acceptance boundary. Do
	// not decode the artifact into verifier structs or consult grading data until
	// the submitted document has passed its own declared interface.
	schemaBytes := mustRead(t, filepath.Join(root, "installer", "architecture.schema.json"))
	artifactBytes := migrationplan.JSON()
	schemaDocument, err := jsonschema.Decode(schemaBytes)
	if err != nil {
		t.Fatalf("installer schema is invalid JSON: %v", err)
	}
	artifactDocument, err := jsonschema.Decode(artifactBytes)
	if err != nil {
		t.Fatalf("architecture artifact is invalid JSON: %v", err)
	}
	if validationErrors := jsonschema.Validate(schemaDocument, artifactDocument); len(validationErrors) != 0 {
		messages := make([]string, len(validationErrors))
		for i, validationErr := range validationErrors {
			messages[i] = validationErr.Error()
		}
		t.Fatalf("architecture does not conform to installer/architecture.schema.json:\n%s", strings.Join(messages, "\n"))
	}

	var plan planDocument
	mustUnmarshal(t, artifactBytes, &plan)
	var estate estateDocument
	mustUnmarshal(t, mustRead(t, filepath.Join(root, "fixtures", "estate.json")), &estate)
	var snapshot snapshotDocument
	mustUnmarshal(t, mustRead(t, filepath.Join(root, "installer", "compatibility-snapshot.json")), &snapshot)

	checks := []struct {
		name  string
		check func() error
	}{
		{"fixed references", func() error { return checkReferences(plan) }},
		{"minimum consolidated topology", func() error { return checkTopology(plan, estate, snapshot) }},
		{"target component placement and sizing", func() error { return checkComponents(plan, estate, snapshot) }},
		{"ordered supported migration paths", func() error { return checkPaths(plan, estate, snapshot) }},
		{"complete content disposition", func() error { return checkContent(plan, estate, snapshot) }},
		{"required migration gates", func() error { return checkGates(plan, snapshot) }},
		{"source support boundaries", func() error { return checkSupport(plan, snapshot) }},
		{"Broadcom research bibliography", func() error { return checkResearch(plan) }},
	}
	for _, tc := range checks {
		t.Run(tc.name, func(t *testing.T) {
			if err := tc.check(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

type planDocument struct {
	SchemaVersion            string       `json:"schema_version"`
	PlanID                   string       `json:"plan_id"`
	InventoryRef             string       `json:"inventory_ref"`
	CompatibilitySnapshotRef string       `json:"compatibility_snapshot_ref"`
	Target                   planTarget   `json:"target"`
	Steps                    []planStep   `json:"steps"`
	Research                 planResearch `json:"research"`
}

type planResearch struct {
	ConsultedOn string           `json:"consulted_on"`
	Sources     []researchSource `json:"sources"`
}

type researchSource struct {
	Publisher   string   `json:"publisher"`
	Title       string   `json:"title"`
	URL         string   `json:"url"`
	ConsultedOn string   `json:"consulted_on"`
	Claims      []string `json:"claims"`
}

type planTarget struct {
	VCFVersion         string          `json:"vcf_version"`
	SiteID             string          `json:"site_id"`
	DeploymentModel    string          `json:"deployment_model"`
	ManagementDomainID string          `json:"management_domain_id"`
	ClusterID          string          `json:"cluster_id"`
	Storage            string          `json:"storage"`
	HostCount          int             `json:"host_count"`
	Components         []planComponent `json:"components"`
}

type planComponent struct {
	ID        string          `json:"id"`
	Product   string          `json:"product"`
	Version   string          `json:"version"`
	Placement planPlacement   `json:"placement"`
	Sizing    componentSizing `json:"sizing"`
}

type planPlacement struct {
	ManagementDomainID string   `json:"management_domain_id"`
	ClusterID          string   `json:"cluster_id"`
	HostIDs            []string `json:"host_ids"`
	AntiAffinity       bool     `json:"anti_affinity"`
}

type componentSizing struct {
	Profile          string `json:"profile"`
	Nodes            int    `json:"nodes"`
	VCPUPerNode      int    `json:"vcpu_per_node"`
	MemoryGiBPerNode int    `json:"memory_gib_per_node"`
	DiskGiBPerNode   int    `json:"disk_gib_per_node"`
}

type endpoint struct {
	ID      string `json:"id"`
	Product string `json:"product"`
	Version string `json:"version"`
}

type planStep struct {
	Order         int           `json:"order"`
	ID            string        `json:"id"`
	Source        endpoint      `json:"source"`
	Target        endpoint      `json:"target"`
	MigrationMode string        `json:"migration_mode"`
	CarryForward  []planCarry   `json:"carry_forward"`
	Abandon       []planAbandon `json:"abandon"`
	Gates         []planGate    `json:"gates"`
	Support       planSupport   `json:"support"`
}

type planCarry struct {
	ItemID      string `json:"item_id"`
	Method      string `json:"method"`
	TargetState string `json:"target_state"`
}

type planAbandon struct {
	ItemID      string `json:"item_id"`
	ReasonCode  string `json:"reason_code"`
	Disposition string `json:"disposition"`
}

type planGate struct {
	ID         string `json:"id"`
	Condition  string `json:"condition"`
	Validation string `json:"validation"`
}

type planSupport struct {
	EndOfGeneralSupport string `json:"end_of_general_support"`
	CutoverPosition     string `json:"cutover_position"`
}

type estateDocument struct {
	Site struct {
		ID              string `json:"id"`
		Topology        string `json:"topology"`
		DeploymentModel string `json:"deployment_model"`
	} `json:"site"`
	ManagementDomain struct {
		ID        string       `json:"id"`
		ClusterID string       `json:"cluster_id"`
		Storage   string       `json:"storage"`
		HostCount int          `json:"host_count"`
		Hosts     []estateHost `json:"hosts"`
	} `json:"management_domain"`
	SourceProducts []estateProduct `json:"source_products"`
}

type estateHost struct {
	ID               string `json:"id"`
	CPUCores         int    `json:"cpu_cores"`
	MemoryGiB        int    `json:"memory_gib"`
	UsableStorageGiB int    `json:"usable_storage_gib"`
}

type estateProduct struct {
	ID      string `json:"id"`
	Product string `json:"product"`
	Version string `json:"version"`
	Content []struct {
		ID string `json:"id"`
	} `json:"content"`
}

type snapshotDocument struct {
	TargetVCFVersion string `json:"target_vcf_version"`
	Topology         struct {
		SiteID             string `json:"site_id"`
		DeploymentModel    string `json:"deployment_model"`
		ManagementDomainID string `json:"management_domain_id"`
		ClusterID          string `json:"cluster_id"`
		Storage            string `json:"storage"`
		MinimumHostCount   int    `json:"minimum_host_count"`
	} `json:"topology"`
	Components []snapshotComponent `json:"components"`
	Paths      []snapshotPath      `json:"paths"`
}

type snapshotComponent struct {
	ID               string   `json:"id"`
	Product          string   `json:"product"`
	Version          string   `json:"version"`
	Profile          string   `json:"profile"`
	Nodes            int      `json:"nodes"`
	VCPUPerNode      int      `json:"vcpu_per_node"`
	MemoryGiBPerNode int      `json:"memory_gib_per_node"`
	DiskGiBPerNode   int      `json:"disk_gib_per_node"`
	HostIDs          []string `json:"host_ids"`
	AntiAffinity     bool     `json:"anti_affinity"`
}

type snapshotPath struct {
	Order               int               `json:"order"`
	StepID              string            `json:"step_id"`
	SourceID            string            `json:"source_id"`
	SourceProduct       string            `json:"source_product"`
	SourceVersion       string            `json:"source_version"`
	TargetComponentID   string            `json:"target_component_id"`
	TargetProduct       string            `json:"target_product"`
	TargetVersion       string            `json:"target_version"`
	MigrationMode       string            `json:"migration_mode"`
	EndOfGeneralSupport string            `json:"end_of_general_support"`
	Carry               []snapshotCarry   `json:"carry"`
	Abandon             []snapshotAbandon `json:"abandon"`
	RequiredGates       []string          `json:"required_gates"`
}

type snapshotCarry struct {
	ItemID string `json:"item_id"`
	Method string `json:"method"`
}

type snapshotAbandon struct {
	ItemID     string `json:"item_id"`
	ReasonCode string `json:"reason_code"`
}

func checkReferences(plan planDocument) error {
	if plan.SchemaVersion != "1.0" || plan.InventoryRef != "fixtures/estate.json" || plan.CompatibilitySnapshotRef != "installer/compatibility-snapshot.json" {
		return fmt.Errorf("artifact references do not identify the fixed schema, inventory, and compatibility snapshot")
	}
	return nil
}

func checkTopology(plan planDocument, estate estateDocument, snapshot snapshotDocument) error {
	want := planTarget{
		VCFVersion:         snapshot.TargetVCFVersion,
		SiteID:             snapshot.Topology.SiteID,
		DeploymentModel:    snapshot.Topology.DeploymentModel,
		ManagementDomainID: snapshot.Topology.ManagementDomainID,
		ClusterID:          snapshot.Topology.ClusterID,
		Storage:            snapshot.Topology.Storage,
		HostCount:          snapshot.Topology.MinimumHostCount,
	}
	got := plan.Target
	got.Components = nil
	if !reflect.DeepEqual(got, want) {
		return fmt.Errorf("target topology = %+v, want pinned topology %+v", got, want)
	}
	if estate.ManagementDomain.HostCount != snapshot.Topology.MinimumHostCount || len(estate.ManagementDomain.Hosts) != snapshot.Topology.MinimumHostCount {
		return fmt.Errorf("fixture does not represent the pinned minimum host count")
	}
	if estate.Site.ID != plan.Target.SiteID || estate.ManagementDomain.ID != plan.Target.ManagementDomainID || estate.ManagementDomain.ClusterID != plan.Target.ClusterID || estate.ManagementDomain.Storage != plan.Target.Storage {
		return fmt.Errorf("target topology does not place components in the inventoried management domain")
	}
	return nil
}

func checkComponents(plan planDocument, estate estateDocument, snapshot snapshotDocument) error {
	if len(plan.Target.Components) != len(snapshot.Components) {
		return fmt.Errorf("got %d target components, want %d", len(plan.Target.Components), len(snapshot.Components))
	}
	hosts := make(map[string]estateHost, len(estate.ManagementDomain.Hosts))
	for _, host := range estate.ManagementDomain.Hosts {
		hosts[host.ID] = host
	}
	allocatedCPU := map[string]int{}
	allocatedMemory := map[string]int{}
	allocatedDisk := map[string]int{}
	actual := map[string]planComponent{}
	for _, component := range plan.Target.Components {
		if _, duplicate := actual[component.ID]; duplicate {
			return fmt.Errorf("target component %q is duplicated", component.ID)
		}
		actual[component.ID] = component
	}
	for _, expected := range snapshot.Components {
		component, ok := actual[expected.ID]
		if !ok {
			return fmt.Errorf("target component %q is missing", expected.ID)
		}
		if component.Product != expected.Product || component.Version != expected.Version {
			return fmt.Errorf("component %q product/version does not match snapshot", expected.ID)
		}
		wantSizing := componentSizing{expected.Profile, expected.Nodes, expected.VCPUPerNode, expected.MemoryGiBPerNode, expected.DiskGiBPerNode}
		if !reflect.DeepEqual(component.Sizing, wantSizing) {
			return fmt.Errorf("component %q sizing = %+v, want %+v", expected.ID, component.Sizing, wantSizing)
		}
		if component.Placement.ManagementDomainID != snapshot.Topology.ManagementDomainID || component.Placement.ClusterID != snapshot.Topology.ClusterID || component.Placement.AntiAffinity != expected.AntiAffinity || !reflect.DeepEqual(component.Placement.HostIDs, expected.HostIDs) {
			return fmt.Errorf("component %q placement does not match the pinned consolidated design", expected.ID)
		}
		if len(component.Placement.HostIDs) != component.Sizing.Nodes {
			return fmt.Errorf("component %q must place each node on an inventoried host", expected.ID)
		}
		for _, hostID := range component.Placement.HostIDs {
			if _, ok := hosts[hostID]; !ok {
				return fmt.Errorf("component %q uses unknown host %q", expected.ID, hostID)
			}
			allocatedCPU[hostID] += component.Sizing.VCPUPerNode
			allocatedMemory[hostID] += component.Sizing.MemoryGiBPerNode
			allocatedDisk[hostID] += component.Sizing.DiskGiBPerNode
		}
	}
	for hostID, host := range hosts {
		if allocatedCPU[hostID] > host.CPUCores || allocatedMemory[hostID] > host.MemoryGiB || allocatedDisk[hostID] > host.UsableStorageGiB {
			return fmt.Errorf("target components exceed capacity of minimum-count host %q", hostID)
		}
	}
	return nil
}

func checkPaths(plan planDocument, estate estateDocument, snapshot snapshotDocument) error {
	if len(plan.Steps) != len(estate.SourceProducts) || len(plan.Steps) != len(snapshot.Paths) {
		return fmt.Errorf("got %d migration steps, want one for each of %d source products", len(plan.Steps), len(estate.SourceProducts))
	}
	for index, expected := range snapshot.Paths {
		step := plan.Steps[index]
		if step.Order != index+1 || step.Order != expected.Order || step.ID != expected.StepID {
			return fmt.Errorf("step %d is not the pinned ordered step %q", index+1, expected.StepID)
		}
		wantSource := endpoint{expected.SourceID, expected.SourceProduct, expected.SourceVersion}
		wantTarget := endpoint{expected.TargetComponentID, expected.TargetProduct, expected.TargetVersion}
		if !reflect.DeepEqual(step.Source, wantSource) || !reflect.DeepEqual(step.Target, wantTarget) || step.MigrationMode != expected.MigrationMode {
			return fmt.Errorf("step %q does not use the pinned source, target, and migration mode", step.ID)
		}
		inventory := estate.SourceProducts[index]
		if inventory.ID != step.Source.ID || inventory.Product != step.Source.Product || inventory.Version != step.Source.Version {
			return fmt.Errorf("step %q does not name the exact inventoried source product and version", step.ID)
		}
	}
	return nil
}

func checkContent(plan planDocument, estate estateDocument, snapshot snapshotDocument) error {
	for index, step := range plan.Steps {
		expectedPath := snapshot.Paths[index]
		inventoryItems := map[string]struct{}{}
		for _, item := range estate.SourceProducts[index].Content {
			inventoryItems[item.ID] = struct{}{}
		}
		seen := map[string]struct{}{}
		carry := map[string]string{}
		for _, item := range step.CarryForward {
			if _, ok := inventoryItems[item.ItemID]; !ok {
				return fmt.Errorf("step %q carries unknown item %q", step.ID, item.ItemID)
			}
			if _, duplicate := seen[item.ItemID]; duplicate {
				return fmt.Errorf("step %q gives item %q more than one disposition", step.ID, item.ItemID)
			}
			seen[item.ItemID] = struct{}{}
			carry[item.ItemID] = item.Method
		}
		abandon := map[string]string{}
		for _, item := range step.Abandon {
			if _, ok := inventoryItems[item.ItemID]; !ok {
				return fmt.Errorf("step %q abandons unknown item %q", step.ID, item.ItemID)
			}
			if _, duplicate := seen[item.ItemID]; duplicate {
				return fmt.Errorf("step %q gives item %q more than one disposition", step.ID, item.ItemID)
			}
			seen[item.ItemID] = struct{}{}
			abandon[item.ItemID] = item.ReasonCode
		}
		if len(seen) != len(inventoryItems) {
			return fmt.Errorf("step %q disposes %d of %d inventoried items", step.ID, len(seen), len(inventoryItems))
		}
		wantCarry := map[string]string{}
		for _, item := range expectedPath.Carry {
			wantCarry[item.ItemID] = item.Method
		}
		wantAbandon := map[string]string{}
		for _, item := range expectedPath.Abandon {
			wantAbandon[item.ItemID] = item.ReasonCode
		}
		if !reflect.DeepEqual(carry, wantCarry) || !reflect.DeepEqual(abandon, wantAbandon) {
			return fmt.Errorf("step %q content compatibility decisions do not match the pinned snapshot", step.ID)
		}
	}
	return nil
}

func checkGates(plan planDocument, snapshot snapshotDocument) error {
	for index, step := range plan.Steps {
		gateIDs := map[string]struct{}{}
		for _, gate := range step.Gates {
			if _, duplicate := gateIDs[gate.ID]; duplicate {
				return fmt.Errorf("step %q duplicates gate %q", step.ID, gate.ID)
			}
			gateIDs[gate.ID] = struct{}{}
		}
		missing := []string{}
		for _, required := range snapshot.Paths[index].RequiredGates {
			if _, ok := gateIDs[required]; !ok {
				missing = append(missing, required)
			}
		}
		if len(missing) != 0 {
			sort.Strings(missing)
			return fmt.Errorf("step %q is missing required gates: %s", step.ID, strings.Join(missing, ", "))
		}
	}
	return nil
}

func checkSupport(plan planDocument, snapshot snapshotDocument) error {
	for index, step := range plan.Steps {
		if step.Support.EndOfGeneralSupport != snapshot.Paths[index].EndOfGeneralSupport || step.Support.CutoverPosition != "before-end-of-general-support" {
			return fmt.Errorf("step %q does not honor the pinned source support boundary", step.ID)
		}
	}
	return nil
}

func checkResearch(plan planDocument) error {
	seenURLs := map[string]struct{}{}
	for _, source := range plan.Research.Sources {
		parsed, err := url.Parse(source.URL)
		if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" || parsed.User != nil {
			return fmt.Errorf("research source %q does not use a valid public HTTPS URL", source.Title)
		}
		host := strings.ToLower(parsed.Hostname())
		if host != "broadcom.com" && !strings.HasSuffix(host, ".broadcom.com") {
			return fmt.Errorf("research source %q is not published on a Broadcom host", source.Title)
		}
		canonicalURL := parsed.String()
		if _, duplicate := seenURLs[canonicalURL]; duplicate {
			return fmt.Errorf("research URL %q is recorded more than once", canonicalURL)
		}
		seenURLs[canonicalURL] = struct{}{}
	}
	return nil
}

func repositoryRoot(t *testing.T) string {
	t.Helper()
	_, filename, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate verifier source")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(filename), ".."))
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return data
}

func mustUnmarshal(t *testing.T, data []byte, destination any) {
	t.Helper()
	if err := json.Unmarshal(data, destination); err != nil {
		t.Fatalf("decode protected input: %v", err)
	}
}
