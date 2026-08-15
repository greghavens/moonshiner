package verifier

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
)

const (
	wantSchemaSHA256   = "0da49df46b56b2cdee67905e5b4f8407d448593be2d3a5de4689bda4ff950e80"
	wantEstateSHA256   = "7467fbb70081a2a61f4a409fdb02a2ac28282e604704563e299b363aa13f13d1"
	wantSnapshotSHA256 = "e200274583365fe215c177b074854de4ef305f778058c49266f4cae6e11c6662"
)

type Plan struct {
	SchemaVersion       string              `json:"schema_version"`
	PlanID              string              `json:"plan_id"`
	TargetVCFVersion    string              `json:"target_vcf_version"`
	Research            Research            `json:"research"`
	StorageDecision     StorageDecision     `json:"storage_decision"`
	TargetPlacement     TargetPlacement     `json:"target_placement"`
	LifecycleBoundaries []LifecycleBoundary `json:"lifecycle_boundaries"`
	Steps               []PlanStep          `json:"steps"`
}

type Research struct {
	AsOf      string           `json:"as_of"`
	Consulted []ResearchSource `json:"consulted"`
}

type ResearchSource struct {
	Publisher string   `json:"publisher"`
	Title     string   `json:"title"`
	URL       string   `json:"url"`
	Accessed  string   `json:"accessed"`
	Supports  []string `json:"supports"`
}

type StorageDecision struct {
	Selected     string               `json:"selected"`
	Rationale    string               `json:"rationale"`
	Alternatives []StorageAlternative `json:"alternatives"`
}

type StorageAlternative struct {
	Architecture                 string `json:"architecture"`
	HostProfile                  string `json:"host_profile"`
	HostCount                    int    `json:"host_count"`
	VSANMinimumGbpsPerHost       int    `json:"vsan_minimum_gbps_per_host"`
	UplinksPerHost               int    `json:"uplinks_per_host"`
	UplinkSpeedGbps              int    `json:"uplink_speed_gbps"`
	UsableCapacityTBAfterReserve int    `json:"usable_capacity_tb_after_reserve"`
}

type TargetPlacement struct {
	ManagementCluster string               `json:"management_cluster"`
	AvailabilityZones int                  `json:"availability_zones"`
	Components        []ComponentPlacement `json:"components"`
	Networks          []PlanNetwork        `json:"networks"`
}

type ComponentPlacement struct {
	ComponentID      string `json:"component_id"`
	Component        string `json:"component"`
	Version          string `json:"version"`
	Cluster          string `json:"cluster"`
	Network          string `json:"network"`
	NodeCount        int    `json:"node_count"`
	Size             string `json:"size"`
	VCPUPerNode      int    `json:"vcpu_per_node"`
	MemoryGiBPerNode int    `json:"memory_gib_per_node"`
	DiskGiBPerNode   int    `json:"disk_gib_per_node"`
}

type PlanNetwork struct {
	Name    string `json:"name"`
	VLAN    int    `json:"vlan"`
	CIDR    string `json:"cidr"`
	MTU     int    `json:"mtu"`
	Purpose string `json:"purpose"`
}

type LifecycleBoundary struct {
	SourceID            string `json:"source_id"`
	Product             string `json:"product"`
	Version             string `json:"version"`
	EndOfGeneralSupport string `json:"end_of_general_support"`
	DesignEffect        string `json:"design_effect"`
}

type PlanStep struct {
	Order    int                  `json:"order"`
	ID       string               `json:"id"`
	Source   *StepSource          `json:"source"`
	Target   StepTarget           `json:"target"`
	Action   string               `json:"action"`
	Content  []ContentDisposition `json:"content"`
	Gates    []Gate               `json:"gates"`
	Rollback string               `json:"rollback"`
}

type StepSource struct {
	ID      string `json:"id"`
	Product string `json:"product"`
	Version string `json:"version"`
}

type StepTarget struct {
	Component string `json:"component"`
	Version   string `json:"version"`
}

type ContentDisposition struct {
	InventoryID string `json:"inventory_id"`
	Disposition string `json:"disposition"`
	Method      string `json:"method"`
	Reason      string `json:"reason"`
}

type Gate struct {
	ID        string `json:"id"`
	Condition string `json:"condition"`
	Evidence  string `json:"evidence"`
}

type Estate struct {
	TargetVCFVersion  string          `json:"target_vcf_version"`
	AvailabilityZones int             `json:"availability_zones"`
	ManagementCluster string          `json:"management_cluster"`
	CapacityModel     CapacityModel   `json:"capacity_model"`
	Networks          []EstateNetwork `json:"networks"`
	Sources           []EstateSource  `json:"sources"`
}

type CapacityModel struct {
	RequiredUsableTBAfterHostFailure int              `json:"required_usable_tb_after_host_failure"`
	MinimumClusterHosts              int              `json:"minimum_cluster_hosts"`
	Options                          []CapacityOption `json:"options"`
}

type CapacityOption struct {
	Architecture                string `json:"architecture"`
	HostProfile                 string `json:"host_profile"`
	UsableTBPerContributingHost int    `json:"usable_tb_per_contributing_host"`
	FailureReserveHosts         int    `json:"failure_reserve_hosts"`
}

type EstateNetwork struct {
	Name string `json:"name"`
	VLAN int    `json:"vlan"`
	CIDR string `json:"cidr"`
	MTU  int    `json:"mtu"`
}

type EstateSource struct {
	ID      string          `json:"id"`
	Product string          `json:"product"`
	Version string          `json:"version"`
	Content []EstateContent `json:"content"`
}

type EstateContent struct {
	ID string `json:"id"`
}

type Snapshot struct {
	TargetVCFVersion string               `json:"target_vcf_version"`
	Storage          SnapshotStorage      `json:"storage"`
	Placements       []ComponentPlacement `json:"placements"`
	Sources          []SnapshotSource     `json:"sources"`
	Steps            []SnapshotStep       `json:"steps"`
}

type SnapshotStorage struct {
	Selected string               `json:"selected"`
	Options  []StorageAlternative `json:"options"`
}

type SnapshotSource struct {
	ID                  string            `json:"id"`
	Product             string            `json:"product"`
	Version             string            `json:"version"`
	TargetComponent     string            `json:"target_component"`
	TargetVersion       string            `json:"target_version"`
	MigrationAction     string            `json:"migration_action"`
	EOGS                string            `json:"eogs"`
	ContentDispositions map[string]string `json:"content_dispositions"`
}

type SnapshotStep struct {
	Order           int      `json:"order"`
	ID              string   `json:"id"`
	SourceID        *string  `json:"source_id"`
	TargetComponent string   `json:"target_component"`
	TargetVersion   string   `json:"target_version"`
	Action          string   `json:"action"`
	Gates           []string `json:"gates"`
}

// TestMigrationPlan is intentionally the only top-level test. It schema-checks
// the artifact before integrity or semantic checks, then runs deterministic,
// table-driven checks using only the artifact and the two pinned fixtures.
func TestMigrationPlan(t *testing.T) {
	root := filepath.Clean("..")
	schemaPath := filepath.Join(root, "installer", "specification", "migration-plan.schema.json")
	artifactPath := filepath.Join(root, "migration-plan.json")
	estatePath := filepath.Join(root, "fixtures", "estate.json")
	snapshotPath := filepath.Join(root, "fixtures", "compatibility-snapshot.json")

	schemaBytes := mustRead(t, schemaPath)
	artifactBytes := mustRead(t, artifactPath)
	schemaDocument := mustDecodeAny(t, schemaBytes, "installer schema")
	artifactDocument := mustDecodeAny(t, artifactBytes, "migration-plan.json")
	schemaObject, ok := schemaDocument.(map[string]any)
	if !ok {
		t.Fatal("installer specification schema is not a JSON object")
	}
	if errs := ValidateJSONSchema(artifactDocument, schemaObject, schemaObject, "$"); len(errs) != 0 {
		if len(errs) > 20 {
			errs = append(errs[:20], fmt.Sprintf("... %d more schema errors", len(errs)-20))
		}
		t.Fatalf("migration-plan.json does not conform to installer/specification/migration-plan.schema.json:\n%s", strings.Join(errs, "\n"))
	}

	// The specification and grading inputs are fixed. These checks occur only
	// after the artifact has passed its installer-owned schema.
	assertSHA256(t, schemaPath, schemaBytes, wantSchemaSHA256)
	estateBytes := mustRead(t, estatePath)
	snapshotBytes := mustRead(t, snapshotPath)
	assertSHA256(t, estatePath, estateBytes, wantEstateSHA256)
	assertSHA256(t, snapshotPath, snapshotBytes, wantSnapshotSHA256)

	var plan Plan
	var estate Estate
	var snapshot Snapshot
	mustUnmarshal(t, artifactBytes, &plan, "migration-plan.json")
	mustUnmarshal(t, estateBytes, &estate, "estate fixture")
	mustUnmarshal(t, snapshotBytes, &snapshot, "compatibility snapshot")

	checks := []struct {
		name string
		run  func() error
	}{
		{"fixed target and topology", func() error { return checkBasics(plan, estate, snapshot) }},
		{"genuine Broadcom research record", func() error { return checkResearch(plan) }},
		{"capacity-derived OSA and ESA decision", func() error { return checkStorage(plan, estate, snapshot) }},
		{"component placement and sizing", func() error { return checkPlacements(plan, snapshot) }},
		{"network placement", func() error { return checkNetworks(plan, estate) }},
		{"source lifecycle boundaries", func() error { return checkLifecycle(plan, estate, snapshot) }},
		{"ordered paths gates and content", func() error { return checkSteps(plan, estate, snapshot) }},
	}
	for _, check := range checks {
		t.Run(check.name, func(t *testing.T) {
			if err := check.run(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func checkBasics(plan Plan, estate Estate, snapshot Snapshot) error {
	if plan.SchemaVersion != "1.0.0" {
		return fmt.Errorf("schema_version: got %q", plan.SchemaVersion)
	}
	if plan.TargetVCFVersion != estate.TargetVCFVersion || plan.TargetVCFVersion != snapshot.TargetVCFVersion {
		return fmt.Errorf("target_vcf_version must be %q", snapshot.TargetVCFVersion)
	}
	if plan.TargetPlacement.ManagementCluster != estate.ManagementCluster {
		return fmt.Errorf("management cluster: got %q, want %q", plan.TargetPlacement.ManagementCluster, estate.ManagementCluster)
	}
	if plan.TargetPlacement.AvailabilityZones != estate.AvailabilityZones {
		return fmt.Errorf("availability zones: got %d, want %d", plan.TargetPlacement.AvailabilityZones, estate.AvailabilityZones)
	}
	if strings.TrimSpace(plan.PlanID) == "" {
		return fmt.Errorf("plan_id must not be blank")
	}
	return nil
}

func checkResearch(plan Plan) error {
	seenURLs := map[string]bool{}
	supports := map[string]bool{}
	for index, source := range plan.Research.Consulted {
		parsed, err := url.Parse(source.URL)
		if err != nil {
			return fmt.Errorf("research source %d URL: %v", index+1, err)
		}
		host := strings.ToLower(parsed.Hostname())
		if host != "broadcom.com" && !strings.HasSuffix(host, ".broadcom.com") {
			return fmt.Errorf("research source %d is not hosted on an official Broadcom domain: %q", index+1, source.URL)
		}
		canonicalURL := host + strings.TrimSuffix(parsed.EscapedPath(), "/")
		if seenURLs[canonicalURL] {
			return fmt.Errorf("research source URL is duplicated: %q", source.URL)
		}
		seenURLs[canonicalURL] = true
		if strings.TrimSpace(source.Title) == "" {
			return fmt.Errorf("research source %d title must not be blank", index+1)
		}
		for _, topic := range source.Supports {
			supports[topic] = true
		}
	}
	for _, required := range []string{"migration-path", "lifecycle"} {
		if !supports[required] {
			return fmt.Errorf("research sources must include support for %q", required)
		}
	}
	return nil
}

func checkStorage(plan Plan, estate Estate, snapshot Snapshot) error {
	if plan.StorageDecision.Selected != snapshot.Storage.Selected {
		return fmt.Errorf("selected storage: got %q, want %q", plan.StorageDecision.Selected, snapshot.Storage.Selected)
	}
	if len(plan.StorageDecision.Alternatives) != len(snapshot.Storage.Options) {
		return fmt.Errorf("storage alternatives: got %d, want %d", len(plan.StorageDecision.Alternatives), len(snapshot.Storage.Options))
	}
	if strings.TrimSpace(plan.StorageDecision.Rationale) == "" {
		return fmt.Errorf("storage rationale must not be blank")
	}
	want := alternativesByArchitecture(snapshot.Storage.Options)
	got := alternativesByArchitecture(plan.StorageDecision.Alternatives)
	for _, model := range estate.CapacityModel.Options {
		calculated := ceilDiv(estate.CapacityModel.RequiredUsableTBAfterHostFailure, model.UsableTBPerContributingHost) + model.FailureReserveHosts
		if calculated < estate.CapacityModel.MinimumClusterHosts {
			calculated = estate.CapacityModel.MinimumClusterHosts
		}
		expected, ok := want[model.Architecture]
		if !ok {
			return fmt.Errorf("snapshot lacks %s storage option", model.Architecture)
		}
		if expected.HostCount != calculated {
			return fmt.Errorf("pinned %s host count %d disagrees with fixture-derived %d", model.Architecture, expected.HostCount, calculated)
		}
		actual, ok := got[model.Architecture]
		if !ok || !reflect.DeepEqual(actual, expected) {
			return fmt.Errorf("%s alternative mismatch: got %+v, want %+v", model.Architecture, actual, expected)
		}
	}
	return nil
}

func checkPlacements(plan Plan, snapshot Snapshot) error {
	if len(plan.TargetPlacement.Components) != len(snapshot.Placements) {
		return fmt.Errorf("component placements: got %d, want %d", len(plan.TargetPlacement.Components), len(snapshot.Placements))
	}
	want := map[string]ComponentPlacement{}
	for _, placement := range snapshot.Placements {
		want[placement.ComponentID] = placement
	}
	seen := map[string]bool{}
	for _, actual := range plan.TargetPlacement.Components {
		if seen[actual.ComponentID] {
			return fmt.Errorf("component placement %q is duplicated", actual.ComponentID)
		}
		seen[actual.ComponentID] = true
		expected, ok := want[actual.ComponentID]
		if !ok {
			return fmt.Errorf("unexpected component placement %q", actual.ComponentID)
		}
		if !reflect.DeepEqual(actual, expected) {
			return fmt.Errorf("placement %q mismatch: got %+v, want %+v", actual.ComponentID, actual, expected)
		}
	}
	return nil
}

func checkNetworks(plan Plan, estate Estate) error {
	if len(plan.TargetPlacement.Networks) != len(estate.Networks) {
		return fmt.Errorf("networks: got %d, want %d", len(plan.TargetPlacement.Networks), len(estate.Networks))
	}
	want := map[string]EstateNetwork{}
	for _, network := range estate.Networks {
		want[network.Name] = network
	}
	seen := map[string]bool{}
	for _, actual := range plan.TargetPlacement.Networks {
		if seen[actual.Name] {
			return fmt.Errorf("network %q is duplicated", actual.Name)
		}
		seen[actual.Name] = true
		expected, ok := want[actual.Name]
		if !ok {
			return fmt.Errorf("unexpected network %q", actual.Name)
		}
		if actual.VLAN != expected.VLAN || actual.CIDR != expected.CIDR || actual.MTU != expected.MTU {
			return fmt.Errorf("network %q mismatch: got vlan=%d cidr=%s mtu=%d", actual.Name, actual.VLAN, actual.CIDR, actual.MTU)
		}
		if strings.TrimSpace(actual.Purpose) == "" {
			return fmt.Errorf("network %q purpose must not be blank", actual.Name)
		}
	}
	return nil
}

func checkLifecycle(plan Plan, estate Estate, snapshot Snapshot) error {
	if len(plan.LifecycleBoundaries) != len(snapshot.Sources) {
		return fmt.Errorf("lifecycle boundaries: got %d, want %d", len(plan.LifecycleBoundaries), len(snapshot.Sources))
	}
	estateByID := map[string]EstateSource{}
	for _, source := range estate.Sources {
		estateByID[source.ID] = source
	}
	want := map[string]SnapshotSource{}
	for _, source := range snapshot.Sources {
		want[source.ID] = source
	}
	seen := map[string]bool{}
	for _, actual := range plan.LifecycleBoundaries {
		if seen[actual.SourceID] {
			return fmt.Errorf("lifecycle source %q is duplicated", actual.SourceID)
		}
		seen[actual.SourceID] = true
		expected, ok := want[actual.SourceID]
		if !ok {
			return fmt.Errorf("unexpected lifecycle source %q", actual.SourceID)
		}
		fixture := estateByID[actual.SourceID]
		if actual.Product != fixture.Product || actual.Version != fixture.Version || actual.EndOfGeneralSupport != expected.EOGS {
			return fmt.Errorf("lifecycle boundary %q mismatch: got %s %s EOGS %s", actual.SourceID, actual.Product, actual.Version, actual.EndOfGeneralSupport)
		}
		if strings.TrimSpace(actual.DesignEffect) == "" {
			return fmt.Errorf("lifecycle source %q design_effect must not be blank", actual.SourceID)
		}
	}
	return nil
}

func checkSteps(plan Plan, estate Estate, snapshot Snapshot) error {
	if len(plan.Steps) != len(snapshot.Steps) {
		return fmt.Errorf("steps: got %d, want %d", len(plan.Steps), len(snapshot.Steps))
	}
	estateByID := map[string]EstateSource{}
	for _, source := range estate.Sources {
		estateByID[source.ID] = source
	}
	snapshotByID := map[string]SnapshotSource{}
	for _, source := range snapshot.Sources {
		snapshotByID[source.ID] = source
	}
	seenContent := map[string]int{}

	for index, expected := range snapshot.Steps {
		actual := plan.Steps[index]
		if actual.Order != expected.Order || actual.Order != index+1 || actual.ID != expected.ID || actual.Action != expected.Action {
			return fmt.Errorf("step %d identity/order/action mismatch: got order=%d id=%q action=%q", index+1, actual.Order, actual.ID, actual.Action)
		}
		if actual.Target.Component != expected.TargetComponent || actual.Target.Version != expected.TargetVersion {
			return fmt.Errorf("step %q target mismatch: got %s %s", actual.ID, actual.Target.Component, actual.Target.Version)
		}
		if strings.TrimSpace(actual.Rollback) == "" {
			return fmt.Errorf("step %q rollback must not be blank", actual.ID)
		}
		actualGates := make([]string, 0, len(actual.Gates))
		for _, gate := range actual.Gates {
			if strings.TrimSpace(gate.Condition) == "" || strings.TrimSpace(gate.Evidence) == "" {
				return fmt.Errorf("step %q gate %q must have a non-blank condition and evidence", actual.ID, gate.ID)
			}
			actualGates = append(actualGates, gate.ID)
		}
		if !sameStringSet(actualGates, expected.Gates) {
			return fmt.Errorf("step %q gates: got %v, want %v", actual.ID, actualGates, expected.Gates)
		}
		if expected.SourceID == nil {
			if actual.Source != nil {
				return fmt.Errorf("step %q must have null source", actual.ID)
			}
			if len(actual.Content) != 0 {
				return fmt.Errorf("platform step %q must not assign inventory content", actual.ID)
			}
			continue
		}
		if actual.Source == nil || actual.Source.ID != *expected.SourceID {
			return fmt.Errorf("step %q source: got %+v, want %q", actual.ID, actual.Source, *expected.SourceID)
		}
		fixture, ok := estateByID[*expected.SourceID]
		if !ok {
			return fmt.Errorf("snapshot step references unknown source %q", *expected.SourceID)
		}
		compatibility := snapshotByID[*expected.SourceID]
		if actual.Source.Product != fixture.Product || actual.Source.Version != fixture.Version {
			return fmt.Errorf("step %q must name source exactly as %s %s", actual.ID, fixture.Product, fixture.Version)
		}
		if actual.Action != compatibility.MigrationAction || actual.Target.Component != compatibility.TargetComponent || actual.Target.Version != compatibility.TargetVersion {
			return fmt.Errorf("step %q violates pinned migration path", actual.ID)
		}
		if len(actual.Content) != len(compatibility.ContentDispositions) {
			return fmt.Errorf("step %q content count: got %d, want %d", actual.ID, len(actual.Content), len(compatibility.ContentDispositions))
		}
		for _, item := range actual.Content {
			expectedDisposition, ok := compatibility.ContentDispositions[item.InventoryID]
			if !ok {
				return fmt.Errorf("step %q contains unknown inventory item %q", actual.ID, item.InventoryID)
			}
			if item.Disposition != expectedDisposition {
				return fmt.Errorf("content %q disposition: got %q, want %q", item.InventoryID, item.Disposition, expectedDisposition)
			}
			if strings.TrimSpace(item.Method) == "" || strings.TrimSpace(item.Reason) == "" {
				return fmt.Errorf("content %q must have a non-blank method and reason", item.InventoryID)
			}
			seenContent[item.InventoryID]++
		}
	}

	for _, source := range estate.Sources {
		for _, item := range source.Content {
			if seenContent[item.ID] != 1 {
				return fmt.Errorf("inventory content %q is assigned %d times; want exactly once", item.ID, seenContent[item.ID])
			}
		}
	}
	return nil
}

func alternativesByArchitecture(values []StorageAlternative) map[string]StorageAlternative {
	result := map[string]StorageAlternative{}
	for _, value := range values {
		result[value.Architecture] = value
	}
	return result
}

func sameStringSet(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	leftCopy := append([]string(nil), left...)
	rightCopy := append([]string(nil), right...)
	sort.Strings(leftCopy)
	sort.Strings(rightCopy)
	return reflect.DeepEqual(leftCopy, rightCopy)
}

func ceilDiv(numerator, denominator int) int {
	return (numerator + denominator - 1) / denominator
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	contents, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return contents
}

func mustDecodeAny(t *testing.T, contents []byte, label string) any {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(contents))
	decoder.UseNumber()
	var result any
	if err := decoder.Decode(&result); err != nil {
		t.Fatalf("decode %s: %v", label, err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		if err == nil {
			t.Fatalf("decode %s: trailing JSON value", label)
		}
		t.Fatalf("decode %s: trailing content: %v", label, err)
	}
	return result
}

func mustUnmarshal(t *testing.T, contents []byte, target any, label string) {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(contents))
	if err := decoder.Decode(target); err != nil {
		t.Fatalf("decode typed %s: %v", label, err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		if err == nil {
			t.Fatalf("decode typed %s: trailing JSON value", label)
		}
		t.Fatalf("decode typed %s: trailing content: %v", label, err)
	}
}

func assertSHA256(t *testing.T, path string, contents []byte, want string) {
	t.Helper()
	digest := sha256.Sum256(contents)
	got := hex.EncodeToString(digest[:])
	if got != want {
		t.Fatalf("protected input %s changed: sha256=%s", path, got)
	}
}
