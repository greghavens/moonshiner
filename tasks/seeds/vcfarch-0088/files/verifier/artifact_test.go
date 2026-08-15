package verifier

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"testing"

	"vcfarch/architecture"
)

const installerSpecSHA256 = "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d"

func TestArchitectureArtifact(t *testing.T) {
	artifactBytes, err := os.ReadFile("../architecture.json")
	if err != nil {
		t.Fatalf("read architecture.json: %v", err)
	}

	// The installer SddcSpec validation is deliberately the first validation.
	// Compatibility, fixture semantics, and package behavior are checked only
	// after this block succeeds.
	artifactValue, err := decodeJSON(artifactBytes)
	if err != nil {
		t.Fatalf("decode architecture.json for SddcSpec validation: %v", err)
	}
	installerBytes, err := os.ReadFile("../specifications/vcf-installer/vcf-installer-openapi.json")
	if err != nil {
		t.Fatalf("read installer OpenAPI document: %v", err)
	}
	hash := sha256.Sum256(installerBytes)
	if hex.EncodeToString(hash[:]) != installerSpecSHA256 {
		t.Fatalf("installer OpenAPI document is not the pinned tag 9.1.0.0 document")
	}
	installerRoot, err := decodeJSON(installerBytes)
	if err != nil {
		t.Fatalf("decode installer OpenAPI document: %v", err)
	}
	sddcSchema, err := resolvePointer(installerRoot, "#/components/schemas/SddcSpec")
	if err != nil {
		t.Fatalf("resolve installer SddcSpec: %v", err)
	}
	if err := validateSchema(installerRoot, sddcSchema, artifactValue, "$"); err != nil {
		t.Fatalf("architecture.json does not validate as installer SddcSpec: %v", err)
	}

	migrationBytes, err := os.ReadFile("../schema/migration-plan.schema.json")
	if err != nil {
		t.Fatalf("read migration plan schema: %v", err)
	}
	migrationSchema, err := decodeJSON(migrationBytes)
	if err != nil {
		t.Fatalf("decode migration plan schema: %v", err)
	}
	if err := validateSchema(migrationSchema, migrationSchema, artifactValue, "$"); err != nil {
		t.Fatalf("architecture.json does not validate as migration plan: %v", err)
	}

	var inventory architecture.Inventory
	loadTyped(t, "../testdata/estate.json", &inventory)
	var snapshot architecture.Snapshot
	loadTyped(t, "../testdata/compatibility-snapshot.json", &snapshot)
	plan, err := architecture.LoadPlan(bytes.NewReader(artifactBytes))
	if err != nil {
		t.Fatalf("architecture.LoadPlan: %v", err)
	}
	if err := verifyPinnedAuthority(plan, inventory, snapshot); err != nil {
		t.Fatalf("pinned artifact verification: %v", err)
	}
	if err := architecture.Validate(plan, inventory, snapshot); err != nil {
		t.Fatalf("architecture.Validate(valid plan): %v", err)
	}

	tests := []struct {
		name   string
		mutate func(*architecture.Plan)
	}{
		{
			name: "unsupported hop",
			mutate: func(candidate *architecture.Plan) {
				candidate.MigrationSteps[0].ToVersion = "5.0.0.0"
			},
		},
		{
			name: "missing estate component",
			mutate: func(candidate *architecture.Plan) {
				candidate.Components = candidate.Components[1:]
			},
		},
		{
			name: "missing technical gate",
			mutate: func(candidate *architecture.Plan) {
				candidate.MigrationSteps[0].Gates = candidate.MigrationSteps[0].Gates[1:]
			},
		},
		{
			name: "undersized Edge",
			mutate: func(candidate *architecture.Plan) {
				candidate.EdgeDesign.FormFactor = "LARGE"
			},
		},
		{
			name: "uplinks share a fabric",
			mutate: func(candidate *architecture.Plan) {
				candidate.EdgeDesign.Uplinks[1].Fabric = candidate.EdgeDesign.Uplinks[0].Fabric
			},
		},
	}
	for _, test := range tests {
		t.Run("package rejects "+test.name, func(t *testing.T) {
			candidate := clonePlan(t, plan)
			test.mutate(&candidate)
			if err := architecture.Validate(candidate, inventory, snapshot); err == nil {
				t.Fatalf("Validate accepted %s", test.name)
			}
		})
	}
}

func verifyPinnedAuthority(plan architecture.Plan, inventory architecture.Inventory, snapshot architecture.Snapshot) error {
	if plan.SddcID != inventory.SddcID || plan.EstateID != inventory.EstateID {
		return fmt.Errorf("artifact does not identify fixture estate")
	}
	if plan.Version != inventory.TargetVCF || plan.TargetVCF != inventory.TargetVCF || snapshot.TargetVCF != inventory.TargetVCF {
		return fmt.Errorf("target VCF version mismatch")
	}
	if plan.WorkflowType != "VCF" {
		return fmt.Errorf("workflowType must be VCF")
	}
	if plan.DNS.Subdomain != inventory.DNSSubdomain || !sameStrings(plan.DNS.Nameservers, inventory.Nameservers) {
		return fmt.Errorf("DNS target does not match fixture")
	}
	if plan.VCenter.Hostname != inventory.VCenter.Hostname || plan.VCenter.RootVCenterPassword != inventory.VCenter.Password || !plan.VCenter.UseExistingDeployment {
		return fmt.Errorf("vCenter target must import the fixture deployment")
	}
	managementMatches := 0
	for _, network := range plan.Networks {
		if network.NetworkType == "MANAGEMENT" && network.VLANID == inventory.ManagementVLAN {
			managementMatches++
		}
	}
	if managementMatches != 1 {
		return fmt.Errorf("expected exactly one fixture management network")
	}

	if err := verifyHops(inventory.VCFVersion, inventory.TargetVCF, snapshot.VCFHops); err != nil {
		return err
	}
	if len(plan.Components) != len(inventory.Components) || len(plan.Components) != len(snapshot.ComponentTargets) {
		return fmt.Errorf("component inventory is not complete")
	}
	inventoryByID := make(map[string]architecture.InventoryComponent, len(inventory.Components))
	for _, component := range inventory.Components {
		if _, duplicate := inventoryByID[component.ID]; duplicate {
			return fmt.Errorf("duplicate fixture component %q", component.ID)
		}
		inventoryByID[component.ID] = component
	}
	targetByID := make(map[string]architecture.ComponentTarget, len(snapshot.ComponentTargets))
	for _, target := range snapshot.ComponentTargets {
		targetByID[target.ID] = target
	}
	seenComponents := make(map[string]bool, len(plan.Components))
	for _, component := range plan.Components {
		if seenComponents[component.ID] {
			return fmt.Errorf("duplicate plan component %q", component.ID)
		}
		seenComponents[component.ID] = true
		current, ok := inventoryByID[component.ID]
		if !ok || component.CurrentVersion != current.Version {
			return fmt.Errorf("component %q current version mismatch", component.ID)
		}
		target, ok := targetByID[component.ID]
		if !ok || component.TargetVersion != target.TargetVersion {
			return fmt.Errorf("component %q target version mismatch", component.ID)
		}
		if !sameStrings(component.Gates, target.RequiredGates) {
			return fmt.Errorf("component %q gates mismatch", component.ID)
		}
	}

	if len(plan.MigrationSteps) != len(snapshot.ComponentTransitions) {
		return fmt.Errorf("expected %d migration steps, got %d", len(snapshot.ComponentTransitions), len(plan.MigrationSteps))
	}
	for i, expected := range snapshot.ComponentTransitions {
		actual := plan.MigrationSteps[i]
		if actual.Order != i+1 || actual.Order != expected.Order ||
			actual.VCFRelease != expected.VCFRelease || actual.Component != expected.Component ||
			actual.FromVersion != expected.FromVersion || actual.ToVersion != expected.ToVersion {
			return fmt.Errorf("migration step %d is not the pinned transition", i+1)
		}
		if !sameStrings(actual.Gates, expected.RequiredGates) {
			return fmt.Errorf("migration step %d gates mismatch", i+1)
		}
	}

	return verifyEdge(plan.EdgeDesign, inventory.DesignInputs, snapshot)
}

func verifyHops(current, target string, hops []architecture.VCFHop) error {
	visited := map[string]bool{}
	for current != target {
		if visited[current] {
			return fmt.Errorf("pinned VCF hop cycle at %s", current)
		}
		visited[current] = true
		next := ""
		for _, hop := range hops {
			if hop.From == current {
				if next != "" {
					return fmt.Errorf("ambiguous pinned hop from %s", current)
				}
				next = hop.To
			}
		}
		if next == "" {
			return fmt.Errorf("no pinned supported hop from %s to %s", current, target)
		}
		current = next
	}
	return nil
}

func verifyEdge(edge architecture.EdgeDesign, input architecture.DesignInputs, snapshot architecture.Snapshot) error {
	if edge.RequiredThroughputGbps != input.RequiredThroughputGbps {
		return fmt.Errorf("Edge throughput does not match fixture")
	}
	formFactor := ""
	for _, band := range snapshot.EdgeSizing {
		if input.RequiredThroughputGbps <= band.MaxThroughputGbps {
			formFactor = band.FormFactor
			break
		}
	}
	if formFactor == "" || edge.FormFactor != formFactor {
		return fmt.Errorf("Edge form factor %q is not the smallest pinned size for %d Gbps", edge.FormFactor, input.RequiredThroughputGbps)
	}
	constraints := snapshot.EdgeConstraints
	if edge.NodeCount != constraints.NodeCount || edge.HAMode != constraints.HAMode || edge.UplinksPerNode != constraints.UplinksPerNode {
		return fmt.Errorf("Edge node, HA, or uplink count mismatch")
	}
	if len(edge.Uplinks) != constraints.UplinksPerNode || len(edge.Uplinks) != len(input.AvailableUplinks) {
		return fmt.Errorf("Edge uplink layout is incomplete")
	}
	seenNames := map[string]bool{}
	seenNICs := map[string]bool{}
	seenFabrics := map[string]bool{}
	seenSwitches := map[string]bool{}
	for _, uplink := range edge.Uplinks {
		if seenNames[uplink.Name] || seenNICs[uplink.PhysicalNIC] {
			return fmt.Errorf("Edge uplink names and physical NICs must be unique")
		}
		seenNames[uplink.Name] = true
		seenNICs[uplink.PhysicalNIC] = true
		if constraints.DistinctFabrics && seenFabrics[uplink.Fabric] {
			return fmt.Errorf("Edge uplinks must use distinct fabrics")
		}
		if constraints.DistinctSwitches && seenSwitches[uplink.Switch] {
			return fmt.Errorf("Edge uplinks must use distinct switches")
		}
		seenFabrics[uplink.Fabric] = true
		seenSwitches[uplink.Switch] = true
		available := false
		for _, candidate := range input.AvailableUplinks {
			if uplink.PhysicalNIC == candidate.PhysicalNIC && uplink.Fabric == candidate.Fabric &&
				uplink.Switch == candidate.Switch && uplink.SpeedGbps == candidate.SpeedGbps {
				available = true
				break
			}
		}
		if !available {
			return fmt.Errorf("uplink %q is not in the fixture", uplink.Name)
		}
		if constraints.SingleUplinkSurvivable && uplink.SpeedGbps < input.RequiredThroughputGbps {
			return fmt.Errorf("uplink %q cannot carry required throughput after a peer failure", uplink.Name)
		}
		if !sameStrings(uplink.Roles, constraints.RequiredRoles) {
			return fmt.Errorf("uplink %q roles mismatch", uplink.Name)
		}
	}
	return nil
}

func loadTyped(t *testing.T, path string, target any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func clonePlan(t *testing.T, plan architecture.Plan) architecture.Plan {
	t.Helper()
	data, err := json.Marshal(plan)
	if err != nil {
		t.Fatal(err)
	}
	var clone architecture.Plan
	if err := json.Unmarshal(data, &clone); err != nil {
		t.Fatal(err)
	}
	return clone
}

func sameStrings(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	leftCopy := append([]string(nil), left...)
	rightCopy := append([]string(nil), right...)
	sort.Strings(leftCopy)
	sort.Strings(rightCopy)
	for i := range leftCopy {
		if leftCopy[i] != rightCopy[i] {
			return false
		}
	}
	return true
}
