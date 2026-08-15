package architecture_test

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"

	"vcfarch/architecture"
)

type inventory struct {
	InventoryID      string `json:"inventory_id"`
	TargetVCFRelease string `json:"target_vcf_release"`
	ManagementDomain struct {
		ID                string `json:"id"`
		ClusterID         string `json:"cluster_id"`
		AvailableCapacity struct {
			VCPU       int `json:"vcpu"`
			MemoryGiB  int `json:"memory_gib"`
			StorageGiB int `json:"storage_gib"`
		} `json:"available_capacity"`
		Traffic struct {
			MeasuredPeakGbps float64 `json:"measured_peak_gbps"`
			GrowthPercent    float64 `json:"growth_percent"`
		} `json:"north_south_traffic"`
		PhysicalUplinks []physicalUplink `json:"physical_uplinks"`
	} `json:"management_domain"`
	Products []inventoryProduct `json:"products"`
}

type physicalUplink struct {
	PhysicalNIC  string  `json:"physical_nic"`
	TOR          string  `json:"tor"`
	SpeedGbps    float64 `json:"speed_gbps"`
	ExternalVLAN int     `json:"external_vlan"`
	TEPVLAN      int     `json:"tep_vlan"`
}

type inventoryProduct struct {
	ID      string           `json:"id"`
	Name    string           `json:"name"`
	Version string           `json:"version"`
	Assets  []inventoryAsset `json:"assets"`
}

type inventoryAsset struct {
	ID   string `json:"id"`
	Kind string `json:"kind"`
	Name string `json:"name"`
}

type snapshot struct {
	SnapshotID       string `json:"snapshot_id"`
	TargetVCFRelease string `json:"target_vcf_release"`
	MigrationOrder   string `json:"migration_order"`
	NetworkRules     struct {
		ReserveFormula            string           `json:"reserve_formula"`
		SelectionPolicy           string           `json:"selection_policy"`
		EdgeFormFactors           []edgeFormFactor `json:"edge_form_factors"`
		EdgeNodeCount             int              `json:"edge_node_count"`
		EdgeHostPlacement         string           `json:"edge_host_placement"`
		Tier0HAMode               string           `json:"tier0_ha_mode"`
		TEPsPerEdge               int              `json:"teps_per_edge"`
		TeamingPolicy             string           `json:"teaming_policy"`
		LAGAllowed                bool             `json:"lag_allowed"`
		MinimumDataUplinksPerEdge int              `json:"minimum_data_uplinks_per_edge"`
		DistinctTORsRequired      bool             `json:"distinct_tors_required"`
	} `json:"network_rules"`
	TargetComponents []targetComponentRule `json:"target_components"`
	MigrationRules   []migrationRule       `json:"migration_rules"`
}

type edgeFormFactor struct {
	Name              string  `json:"name"`
	MaxThroughputGbps float64 `json:"max_throughput_gbps"`
	Production        bool    `json:"production"`
}

type targetComponentRule struct {
	Name             string    `json:"name"`
	Version          string    `json:"version"`
	DomainID         string    `json:"domain_id"`
	ClusterID        string    `json:"cluster_id"`
	AvailabilityRule string    `json:"availability_rule"`
	NodeSets         []nodeSet `json:"node_sets"`
}

type nodeSet struct {
	Role           string `json:"role"`
	Count          int    `json:"count"`
	Profile        string `json:"profile"`
	VCPUEach       int    `json:"vcpu_each"`
	MemoryGiBEach  int    `json:"memory_gib_each"`
	StorageGiBEach int    `json:"storage_gib_each"`
}

type migrationRule struct {
	SourceName          string        `json:"source_name"`
	SourceVersion       string        `json:"source_version"`
	TargetComponent     string        `json:"target_component"`
	TargetVersion       string        `json:"target_version"`
	Supported           bool          `json:"supported"`
	Method              string        `json:"method"`
	EndOfGeneralSupport string        `json:"end_of_general_support"`
	PreGates            []string      `json:"pre_gates"`
	PostGates           []string      `json:"post_gates"`
	ContentRules        []contentRule `json:"content_rules"`
}

type contentRule struct {
	Selector struct {
		Kind string `json:"kind"`
	} `json:"selector"`
	Action    string `json:"action"`
	Mechanism string `json:"mechanism"`
	Target    string `json:"target"`
}

type semanticPlan struct {
	SchemaVersion string `json:"schema_version"`
	PlanID        string `json:"plan_id"`
	GeneratedFrom struct {
		InventoryID           string `json:"inventory_id"`
		CompatibilitySnapshot string `json:"compatibility_snapshot"`
		TargetVCFRelease      string `json:"target_vcf_release"`
	} `json:"generated_from"`
	Design struct {
		ComponentPlacements []componentPlacement `json:"component_placements"`
		Network             networkDesign        `json:"network"`
	} `json:"design"`
	MigrationSteps []migrationStep `json:"migration_steps"`
}

type componentPlacement struct {
	Component        string    `json:"component"`
	Version          string    `json:"version"`
	DomainID         string    `json:"domain_id"`
	ClusterID        string    `json:"cluster_id"`
	AvailabilityRule string    `json:"availability_rule"`
	NodeSets         []nodeSet `json:"node_sets"`
}

type networkDesign struct {
	RequiredThroughputGbps float64      `json:"required_throughput_gbps"`
	EdgeFormFactor         string       `json:"edge_form_factor"`
	FormFactorCapacityGbps float64      `json:"form_factor_capacity_gbps"`
	EdgeNodeCount          int          `json:"edge_node_count"`
	Tier0HAMode            string       `json:"tier0_ha_mode"`
	TEPsPerEdge            int          `json:"teps_per_edge"`
	TeamingPolicy          string       `json:"teaming_policy"`
	LAG                    bool         `json:"lag"`
	EdgeNodes              []edgeNode   `json:"edge_nodes"`
	Uplinks                []planUplink `json:"uplinks"`
}

type edgeNode struct {
	Name          string   `json:"name"`
	ClusterID     string   `json:"cluster_id"`
	HostPlacement string   `json:"host_placement"`
	Uplinks       []string `json:"uplinks"`
}

type planUplink struct {
	Name         string  `json:"name"`
	PhysicalNIC  string  `json:"physical_nic"`
	TOR          string  `json:"tor"`
	SpeedGbps    float64 `json:"speed_gbps"`
	ExternalVLAN int     `json:"external_vlan"`
	TEPVLAN      int     `json:"tep_vlan"`
}

type migrationStep struct {
	Sequence int    `json:"sequence"`
	ID       string `json:"id"`
	Source   struct {
		InventoryProductID string `json:"inventory_product_id"`
		Name               string `json:"name"`
		Version            string `json:"version"`
	} `json:"source"`
	Target struct {
		Component string `json:"component"`
		Version   string `json:"version"`
	} `json:"target"`
	Method              string               `json:"method"`
	EndOfGeneralSupport string               `json:"end_of_general_support"`
	PreGates            []gate               `json:"pre_gates"`
	PostGates           []gate               `json:"post_gates"`
	Content             []contentDisposition `json:"content"`
}

type gate struct {
	ID       string `json:"id"`
	Evidence string `json:"evidence"`
}

type contentDisposition struct {
	AssetID   string `json:"asset_id"`
	AssetName string `json:"asset_name"`
	Kind      string `json:"kind"`
	Action    string `json:"action"`
	Mechanism string `json:"mechanism"`
	Target    string `json:"target"`
}

func loadJSON(t *testing.T, path string, target any) []byte {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(raw, target); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
	return raw
}

func TestPlanSemantics(t *testing.T) {
	// installer/verify.sh has already validated the complete artifact against
	// installer/plan.schema.json. Semantic checks deliberately ignore research.
	var plan semanticPlan
	planRaw := loadJSON(t, "../migration-plan.json", &plan)
	var estate inventory
	loadJSON(t, "../installer/estate.json", &estate)
	var authority snapshot
	loadJSON(t, "../installer/compatibility-snapshot.json", &authority)

	checks := []struct {
		name string
		run  func() error
	}{
		{"pinned provenance", func() error { return checkProvenance(plan, estate, authority) }},
		{"component placement and sizing", func() error { return checkPlacements(plan, estate, authority) }},
		{"throughput-driven edge and uplinks", func() error { return checkNetwork(plan, estate, authority) }},
		{"ordered product migrations", func() error { return checkMigrations(plan, estate, authority) }},
		{"go builder matches artifact", func() error { return checkBuilder(planRaw) }},
	}
	for _, check := range checks {
		t.Run(check.name, func(t *testing.T) {
			if err := check.run(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestBuildPlanUsesSuppliedInputs(t *testing.T) {
	var estate inventory
	loadJSON(t, "../installer/estate.json", &estate)
	var authority snapshot
	loadJSON(t, "../installer/compatibility-snapshot.json", &authority)

	estate.InventoryID = "alternate-estate"
	authority.SnapshotID = "alternate-snapshot"
	estate.ManagementDomain.Traffic.MeasuredPeakGbps = 1
	estate.ManagementDomain.Traffic.GrowthPercent = 0
	sort.Slice(authority.NetworkRules.EdgeFormFactors, func(left, right int) bool {
		return authority.NetworkRules.EdgeFormFactors[left].MaxThroughputGbps > authority.NetworkRules.EdgeFormFactors[right].MaxThroughputGbps
	})
	estate.Products = []inventoryProduct{estate.Products[2], estate.Products[0], estate.Products[1]}
	estate.Products[0].Assets[0].Name = "Alternate recent log window"

	directory := t.TempDir()
	inventoryPath := filepath.Join(directory, "estate.json")
	compatibilityPath := filepath.Join(directory, "compatibility.json")
	writeJSON(t, inventoryPath, estate)
	writeJSON(t, compatibilityPath, authority)

	built, err := architecture.BuildPlan(inventoryPath, compatibilityPath)
	if err != nil {
		t.Fatalf("BuildPlan with alternate inputs: %v", err)
	}
	raw, err := json.Marshal(built)
	if err != nil {
		t.Fatalf("marshal alternate plan: %v", err)
	}
	var plan semanticPlan
	if err := json.Unmarshal(raw, &plan); err != nil {
		t.Fatalf("decode alternate plan: %v", err)
	}
	if plan.GeneratedFrom.InventoryID != estate.InventoryID || plan.GeneratedFrom.CompatibilitySnapshot != authority.SnapshotID {
		t.Fatalf("BuildPlan ignored alternate provenance inputs")
	}
	if plan.Design.Network.RequiredThroughputGbps != 1 || plan.Design.Network.EdgeFormFactor != "medium" || plan.Design.Network.FormFactorCapacityGbps != 2 {
		t.Fatalf("BuildPlan did not derive the alternate throughput and Edge size: %#v", plan.Design.Network)
	}
	if len(plan.MigrationSteps) != len(estate.Products) || plan.MigrationSteps[0].Source.InventoryProductID != "logs-prod" {
		t.Fatalf("BuildPlan did not preserve alternate inventory order")
	}
	foundAlternateAsset := false
	for _, disposition := range plan.MigrationSteps[0].Content {
		if disposition.AssetID == "logs-history-recent-90d" && disposition.AssetName == "Alternate recent log window" {
			foundAlternateAsset = true
		}
	}
	if !foundAlternateAsset {
		t.Fatalf("BuildPlan did not derive content from the alternate inventory")
	}
}

func writeJSON(t *testing.T, path string, value any) {
	t.Helper()
	raw, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("encode %s: %v", path, err)
	}
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatalf("write %s: %v", path, err)
	}
}

func checkProvenance(plan semanticPlan, estate inventory, authority snapshot) error {
	if plan.SchemaVersion != "1.0" {
		return fmt.Errorf("schema_version = %q, want 1.0", plan.SchemaVersion)
	}
	if plan.GeneratedFrom.InventoryID != estate.InventoryID {
		return fmt.Errorf("inventory provenance = %q, want %q", plan.GeneratedFrom.InventoryID, estate.InventoryID)
	}
	if plan.GeneratedFrom.CompatibilitySnapshot != authority.SnapshotID {
		return fmt.Errorf("snapshot provenance = %q, want %q", plan.GeneratedFrom.CompatibilitySnapshot, authority.SnapshotID)
	}
	if plan.GeneratedFrom.TargetVCFRelease != estate.TargetVCFRelease || plan.GeneratedFrom.TargetVCFRelease != authority.TargetVCFRelease {
		return fmt.Errorf("target release is not the common pinned target")
	}
	return nil
}

func checkPlacements(plan semanticPlan, estate inventory, authority snapshot) error {
	if len(plan.Design.ComponentPlacements) != len(authority.TargetComponents) {
		return fmt.Errorf("got %d placements, want %d", len(plan.Design.ComponentPlacements), len(authority.TargetComponents))
	}
	placements := make(map[string]componentPlacement)
	for _, placement := range plan.Design.ComponentPlacements {
		if _, exists := placements[placement.Component]; exists {
			return fmt.Errorf("duplicate placement for %s", placement.Component)
		}
		placements[placement.Component] = placement
	}
	usedVCPU, usedMemory, usedStorage := 0, 0, 0
	for _, want := range authority.TargetComponents {
		got, ok := placements[want.Name]
		if !ok {
			return fmt.Errorf("missing placement for %s", want.Name)
		}
		if got.Version != want.Version || got.DomainID != want.DomainID || got.ClusterID != want.ClusterID || got.AvailabilityRule != want.AvailabilityRule {
			return fmt.Errorf("placement metadata for %s does not match snapshot", want.Name)
		}
		if !sameNodeSets(got.NodeSets, want.NodeSets) {
			return fmt.Errorf("node sets for %s do not match snapshot: got %#v want %#v", want.Name, got.NodeSets, want.NodeSets)
		}
		for _, nodes := range got.NodeSets {
			usedVCPU += nodes.Count * nodes.VCPUEach
			usedMemory += nodes.Count * nodes.MemoryGiBEach
			usedStorage += nodes.Count * nodes.StorageGiBEach
		}
	}
	capacity := estate.ManagementDomain.AvailableCapacity
	if usedVCPU > capacity.VCPU || usedMemory > capacity.MemoryGiB || usedStorage > capacity.StorageGiB {
		return fmt.Errorf("target placement exceeds management capacity: used %d vCPU/%d GiB/%d GiB", usedVCPU, usedMemory, usedStorage)
	}
	return nil
}

func checkNetwork(plan semanticPlan, estate inventory, authority snapshot) error {
	rules := authority.NetworkRules
	if rules.ReserveFormula != "measured_peak_times_one_plus_growth_percent" {
		return fmt.Errorf("unsupported frozen reserve formula %q", rules.ReserveFormula)
	}
	if rules.SelectionPolicy != "smallest_production_form_factor_meeting_required_throughput" {
		return fmt.Errorf("unsupported frozen Edge selection policy %q", rules.SelectionPolicy)
	}
	required := estate.ManagementDomain.Traffic.MeasuredPeakGbps * (1 + estate.ManagementDomain.Traffic.GrowthPercent/100)
	if math.Abs(plan.Design.Network.RequiredThroughputGbps-required) > 1e-9 {
		return fmt.Errorf("required throughput = %v, want %v", plan.Design.Network.RequiredThroughputGbps, required)
	}
	var selected *edgeFormFactor
	for index := range rules.EdgeFormFactors {
		candidate := &rules.EdgeFormFactors[index]
		if candidate.Production && candidate.MaxThroughputGbps >= required &&
			(selected == nil || candidate.MaxThroughputGbps < selected.MaxThroughputGbps) {
			selected = candidate
		}
	}
	if selected == nil {
		return fmt.Errorf("no production Edge form factor satisfies %.2f Gbps", required)
	}
	network := plan.Design.Network
	if network.EdgeFormFactor != selected.Name || network.FormFactorCapacityGbps != selected.MaxThroughputGbps {
		return fmt.Errorf("Edge form factor/capacity = %s/%v, want %s/%v", network.EdgeFormFactor, network.FormFactorCapacityGbps, selected.Name, selected.MaxThroughputGbps)
	}
	if network.EdgeNodeCount != rules.EdgeNodeCount || network.Tier0HAMode != rules.Tier0HAMode || network.TEPsPerEdge != rules.TEPsPerEdge || network.TeamingPolicy != rules.TeamingPolicy {
		return fmt.Errorf("Edge HA, TEP, or teaming settings do not match snapshot")
	}
	if network.LAG != rules.LAGAllowed {
		return fmt.Errorf("lag = %v, want %v", network.LAG, rules.LAGAllowed)
	}
	if len(network.EdgeNodes) != rules.EdgeNodeCount {
		return fmt.Errorf("got %d Edge nodes, want %d", len(network.EdgeNodes), rules.EdgeNodeCount)
	}
	if len(network.Uplinks) != len(estate.ManagementDomain.PhysicalUplinks) || len(network.Uplinks) < rules.MinimumDataUplinksPerEdge {
		return fmt.Errorf("uplink count does not provide the pinned dual-uplink layout")
	}
	uplinkNames := make([]string, 0, len(network.Uplinks))
	uplinkNameSeen := map[string]bool{}
	physicalPaths := make(map[string]physicalUplink, len(estate.ManagementDomain.PhysicalUplinks))
	for _, path := range estate.ManagementDomain.PhysicalUplinks {
		physicalPaths[path.PhysicalNIC] = path
	}
	tors := map[string]bool{}
	usedPhysicalPaths := map[string]bool{}
	for _, got := range network.Uplinks {
		if got.Name == "" || uplinkNameSeen[got.Name] {
			return fmt.Errorf("uplink names must be nonempty and unique")
		}
		uplinkNameSeen[got.Name] = true
		want, exists := physicalPaths[got.PhysicalNIC]
		if !exists || usedPhysicalPaths[got.PhysicalNIC] || got.TOR != want.TOR || got.SpeedGbps != want.SpeedGbps || got.ExternalVLAN != want.ExternalVLAN || got.TEPVLAN != want.TEPVLAN {
			return fmt.Errorf("uplink %q does not map exactly once to an available physical path", got.Name)
		}
		usedPhysicalPaths[got.PhysicalNIC] = true
		uplinkNames = append(uplinkNames, got.Name)
		tors[got.TOR] = true
	}
	if rules.DistinctTORsRequired && len(tors) != len(network.Uplinks) {
		return fmt.Errorf("uplinks are not split across distinct ToRs")
	}
	nodeNames := map[string]bool{}
	for _, node := range network.EdgeNodes {
		if nodeNames[node.Name] {
			return fmt.Errorf("duplicate Edge node %q", node.Name)
		}
		nodeNames[node.Name] = true
		if node.ClusterID != estate.ManagementDomain.ClusterID || node.HostPlacement != rules.EdgeHostPlacement {
			return fmt.Errorf("Edge node %s lacks management-cluster anti-affinity placement", node.Name)
		}
		if !sameStringSet(node.Uplinks, uplinkNames) {
			return fmt.Errorf("Edge node %s is not attached to every required uplink", node.Name)
		}
	}
	return nil
}

func checkMigrations(plan semanticPlan, estate inventory, authority snapshot) error {
	if authority.MigrationOrder != "inventory_product_order" {
		return fmt.Errorf("unsupported frozen migration order %q", authority.MigrationOrder)
	}
	if len(plan.MigrationSteps) != len(estate.Products) || len(plan.MigrationSteps) != len(authority.MigrationRules) {
		return fmt.Errorf("got %d migration steps for %d products", len(plan.MigrationSteps), len(estate.Products))
	}
	seenProducts := map[string]bool{}
	abandoned := 0
	for index, product := range estate.Products {
		step := plan.MigrationSteps[index]
		rule, ruleExists := migrationRuleFor(authority.MigrationRules, product.Name, product.Version)
		if step.Sequence != index+1 {
			return fmt.Errorf("step %d has sequence %d", index, step.Sequence)
		}
		if seenProducts[step.Source.InventoryProductID] {
			return fmt.Errorf("product %s appears more than once", step.Source.InventoryProductID)
		}
		seenProducts[step.Source.InventoryProductID] = true
		if step.Source.InventoryProductID != product.ID || step.Source.Name != product.Name || step.Source.Version != product.Version {
			return fmt.Errorf("step %d source does not match inventory product %#v", index+1, product)
		}
		if !ruleExists || !rule.Supported {
			return fmt.Errorf("snapshot has no supported exact-version rule for %s %s", product.Name, product.Version)
		}
		if step.Target.Component != rule.TargetComponent || step.Target.Version != rule.TargetVersion || step.Method != rule.Method || step.EndOfGeneralSupport != rule.EndOfGeneralSupport {
			return fmt.Errorf("step %d target, method, or support boundary differs from snapshot", index+1)
		}
		if !reflect.DeepEqual(gateIDs(step.PreGates), rule.PreGates) || !reflect.DeepEqual(gateIDs(step.PostGates), rule.PostGates) {
			return fmt.Errorf("step %d gates differ from the pinned technical gates", index+1)
		}
		if len(step.Content) != len(product.Assets) {
			return fmt.Errorf("step %d has %d dispositions for %d assets", index+1, len(step.Content), len(product.Assets))
		}
		seenAssets := map[string]bool{}
		dispositions := make(map[string]contentDisposition, len(step.Content))
		for _, disposition := range step.Content {
			if seenAssets[disposition.AssetID] {
				return fmt.Errorf("asset %s has multiple dispositions", disposition.AssetID)
			}
			seenAssets[disposition.AssetID] = true
			dispositions[disposition.AssetID] = disposition
		}
		for assetIndex, asset := range product.Assets {
			disposition, exists := dispositions[asset.ID]
			if !exists {
				return fmt.Errorf("asset %s has no disposition", asset.ID)
			}
			if disposition.AssetID != asset.ID || disposition.AssetName != asset.Name || disposition.Kind != asset.Kind {
				return fmt.Errorf("disposition %d in step %d does not match inventory asset %#v", assetIndex+1, index+1, asset)
			}
			contentRule, ok := ruleForKind(rule.ContentRules, asset.Kind)
			if !ok {
				return fmt.Errorf("no compatibility rule for %s asset %s", asset.Kind, asset.ID)
			}
			if disposition.Action != contentRule.Action || disposition.Mechanism != contentRule.Mechanism || disposition.Target != contentRule.Target {
				return fmt.Errorf("asset %s disposition differs from pinned compatibility", asset.ID)
			}
			if disposition.Action == "abandon" {
				abandoned++
			}
		}
	}
	if len(seenProducts) != len(estate.Products) {
		return fmt.Errorf("not every product appears exactly once")
	}
	if abandoned == 0 {
		return fmt.Errorf("plan does not identify any non-transferable estate content")
	}
	return nil
}

func checkBuilder(artifactRaw []byte) error {
	built, err := architecture.BuildPlan("../installer/estate.json", "../installer/compatibility-snapshot.json")
	if err != nil {
		return fmt.Errorf("BuildPlan: %w", err)
	}
	builtRaw, err := json.Marshal(built)
	if err != nil {
		return fmt.Errorf("marshal BuildPlan result: %w", err)
	}
	var artifact, generated any
	if err := json.Unmarshal(artifactRaw, &artifact); err != nil {
		return err
	}
	if err := json.Unmarshal(builtRaw, &generated); err != nil {
		return err
	}
	if !reflect.DeepEqual(generated, artifact) {
		return fmt.Errorf("BuildPlan result is not semantically identical to migration-plan.json")
	}
	return nil
}

func gateIDs(gates []gate) []string {
	ids := make([]string, len(gates))
	for index, value := range gates {
		ids[index] = value.ID
	}
	return ids
}

func ruleForKind(rules []contentRule, kind string) (contentRule, bool) {
	for _, rule := range rules {
		if rule.Selector.Kind == kind {
			return rule, true
		}
	}
	return contentRule{}, false
}

func migrationRuleFor(rules []migrationRule, name, version string) (migrationRule, bool) {
	for _, rule := range rules {
		if rule.SourceName == name && rule.SourceVersion == version {
			return rule, true
		}
	}
	return migrationRule{}, false
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

func sameNodeSets(left, right []nodeSet) bool {
	if len(left) != len(right) {
		return false
	}
	rightByRole := make(map[string]nodeSet, len(right))
	for _, value := range right {
		rightByRole[value.Role] = value
	}
	seen := map[string]bool{}
	for _, value := range left {
		if seen[value.Role] || !reflect.DeepEqual(value, rightByRole[value.Role]) {
			return false
		}
		seen[value.Role] = true
	}
	return true
}
