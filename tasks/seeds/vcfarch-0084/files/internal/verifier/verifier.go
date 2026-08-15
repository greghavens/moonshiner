package verifier

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"reflect"

	"vcfarch"
)

// VerifyPaths performs deterministic, offline verification. Installer schema
// validation intentionally precedes plan-schema and semantic verification.
func VerifyPaths(artifactPath, openAPIPath, planSchemaPath, inventoryPath, snapshotPath string) error {
	artifact, err := os.ReadFile(artifactPath)
	if err != nil {
		return fmt.Errorf("read architecture: %w", err)
	}
	openAPI, err := os.ReadFile(openAPIPath)
	if err != nil {
		return fmt.Errorf("read installer OpenAPI: %w", err)
	}
	if err := validateSddcSpec(artifact, openAPI); err != nil {
		return fmt.Errorf("installer schema: %w", err)
	}

	planSchema, err := os.ReadFile(planSchemaPath)
	if err != nil {
		return fmt.Errorf("read migration-plan schema: %w", err)
	}
	inventory, err := os.ReadFile(inventoryPath)
	if err != nil {
		return fmt.Errorf("read inventory: %w", err)
	}
	snapshot, err := os.ReadFile(snapshotPath)
	if err != nil {
		return fmt.Errorf("read compatibility snapshot: %w", err)
	}
	return VerifyBytes(artifact, openAPI, planSchema, inventory, snapshot)
}

func VerifyBytes(artifact, openAPI, planSchema, inventoryJSON, snapshotJSON []byte) error {
	if err := validateSddcSpec(artifact, openAPI); err != nil {
		return fmt.Errorf("installer schema: %w", err)
	}
	if err := verifyProtectedInputs(openAPI, planSchema, inventoryJSON, snapshotJSON); err != nil {
		return err
	}
	var envelope struct {
		MigrationPlan json.RawMessage `json:"x-migrationPlan"`
	}
	if err := json.Unmarshal(artifact, &envelope); err != nil {
		return fmt.Errorf("decode architecture envelope: %w", err)
	}
	if len(envelope.MigrationPlan) == 0 {
		return fmt.Errorf("migration-plan schema: x-migrationPlan is missing")
	}
	if err := validateJSONSchema(envelope.MigrationPlan, planSchema); err != nil {
		return fmt.Errorf("migration-plan schema: %w", err)
	}

	var architecture vcfarch.Architecture
	var inventory vcfarch.Inventory
	var snapshot vcfarch.CompatibilitySnapshot
	if err := json.Unmarshal(artifact, &architecture); err != nil {
		return fmt.Errorf("decode architecture: %w", err)
	}
	if err := json.Unmarshal(inventoryJSON, &inventory); err != nil {
		return fmt.Errorf("decode inventory: %w", err)
	}
	if err := json.Unmarshal(snapshotJSON, &snapshot); err != nil {
		return fmt.Errorf("decode compatibility snapshot: %w", err)
	}
	return verifySemantics(architecture, inventory, snapshot)
}

func verifyProtectedInputs(openAPI, planSchema, inventory, snapshot []byte) error {
	inputs := []struct {
		name string
		raw  []byte
		want string
	}{
		{name: "installer OpenAPI", raw: openAPI, want: "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d"},
		{name: "migration-plan schema", raw: planSchema, want: "915ed79fb13d34c7640db274fbfd8521c8a453b6fb259a5eb100a8715e7673e8"},
		{name: "estate inventory", raw: inventory, want: "8c681e074140d9618d75c41e3322a0435336cc35a81037291408d33fffe18910"},
		{name: "compatibility snapshot", raw: snapshot, want: "0ccb20024bbda2e75c6b390b417ba5dedc5599cb21bb25eab4885c468dd4f40e"},
	}
	for _, input := range inputs {
		got := fmt.Sprintf("%x", sha256.Sum256(input.raw))
		if got != input.want {
			return fmt.Errorf("protected %s digest mismatch", input.name)
		}
	}
	return nil
}

func verifySemantics(architecture vcfarch.Architecture, inventory vcfarch.Inventory, snapshot vcfarch.CompatibilitySnapshot) error {
	if architecture.SddcID != inventory.EstateID {
		return fmt.Errorf("SddcSpec sddcId %q does not match estate %q", architecture.SddcID, inventory.EstateID)
	}
	if architecture.Version != snapshot.TargetVCF {
		return fmt.Errorf("SddcSpec version %q does not match target %q", architecture.Version, snapshot.TargetVCF)
	}
	if architecture.WorkflowType != "VCF" {
		return fmt.Errorf("SddcSpec workflowType must be VCF")
	}
	if architecture.VCenterSpec.VCenterHostname != inventory.VCenter.Hostname ||
		architecture.VCenterSpec.SSLThumbprint != inventory.VCenter.SSLThumbprint ||
		!architecture.VCenterSpec.UseExistingDeployment ||
		architecture.VCenterSpec.Version != snapshot.TargetVCF {
		return fmt.Errorf("SddcSpec vcenterSpec does not describe the existing vCenter at the target version")
	}
	if architecture.ClusterSpec.ClusterName != inventory.ManagementDomain.ClusterID {
		return fmt.Errorf("SddcSpec clusterName does not match the management cluster")
	}
	if architecture.DatastoreSpec.ExistingDatastoreName != inventory.ManagementDomain.DatastoreName {
		return fmt.Errorf("SddcSpec datastore does not match the existing management datastore")
	}
	if !reflect.DeepEqual(architecture.NetworkSpecs, inventory.Networks) ||
		!reflect.DeepEqual(architecture.DNSSpec, inventory.DNS) ||
		!reflect.DeepEqual(architecture.NTPServers, inventory.NTPServers) {
		return fmt.Errorf("SddcSpec network, DNS, or NTP design differs from the inventory")
	}

	plan := architecture.MigrationPlan
	if plan.EstateID != inventory.EstateID || plan.SourceVCFVersion != inventory.VCFVersion || plan.TargetVCFVersion != snapshot.TargetVCF {
		return fmt.Errorf("migration plan estate or endpoint version mismatch")
	}
	expectedPath := []string{snapshot.SourceVCF}
	currentVCF := snapshot.SourceVCF
	for _, edge := range snapshot.UpgradeEdges {
		if edge.FromVCF != currentVCF {
			return fmt.Errorf("compatibility snapshot has a disconnected edge from %q", edge.FromVCF)
		}
		expectedPath = append(expectedPath, edge.ToVCF)
		currentVCF = edge.ToVCF
	}
	if currentVCF != snapshot.TargetVCF || !reflect.DeepEqual(plan.UpgradePath, expectedPath) {
		return fmt.Errorf("upgrade path %v does not match supported path %v", plan.UpgradePath, expectedPath)
	}
	if err := verifyTopology(plan.TargetTopology, inventory); err != nil {
		return err
	}
	return verifySteps(plan.Steps, inventory, snapshot)
}

func verifyTopology(topology vcfarch.TargetTopology, inventory vcfarch.Inventory) error {
	domain := inventory.ManagementDomain
	if topology.ManagementDomainID != domain.ID || topology.ClusterID != domain.ClusterID || !topology.Stretched {
		return fmt.Errorf("target topology does not preserve the stretched management domain")
	}
	if !reflect.DeepEqual(topology.DataSites, domain.DataSites) || len(topology.DataSites) != 2 {
		return fmt.Errorf("target topology must preserve exactly the two inventory data sites")
	}
	if topology.Witness.ComponentID != domain.WitnessComponentID || topology.Witness.SiteID != inventory.IndependentWitness {
		return fmt.Errorf("witness is not placed at the independent witness location")
	}
	for _, dataSite := range topology.DataSites {
		if topology.Witness.SiteID == dataSite {
			return fmt.Errorf("witness site must be outside both data sites")
		}
	}
	for _, site := range inventory.Sites {
		if site.ID == topology.Witness.SiteID {
			if site.Role != "witness" || site.FailureDomain != topology.Witness.FailureDomain {
				return fmt.Errorf("witness failure domain does not match the independent witness site")
			}
			return nil
		}
	}
	return fmt.Errorf("witness site %q is absent from the inventory", topology.Witness.SiteID)
}

func verifySteps(steps []vcfarch.PlanStep, inventory vcfarch.Inventory, snapshot vcfarch.CompatibilitySnapshot) error {
	components := make(map[string]vcfarch.Component, len(inventory.Components))
	versions := make(map[string]string, len(inventory.Components))
	sites := make(map[string]string, len(inventory.Components))
	for _, component := range inventory.Components {
		if _, duplicate := components[component.ID]; duplicate {
			return fmt.Errorf("duplicate inventory component %q", component.ID)
		}
		components[component.ID] = component
		versions[component.ID] = component.Version
		sites[component.ID] = component.Site
	}

	stepIndex := 0
	for phaseIndex, edge := range snapshot.UpgradeEdges {
		phase := phaseIndex + 1
		rules := map[string]vcfarch.SequenceRule{}
		for _, rule := range edge.Sequence {
			rules[rule.ComponentType] = rule
		}
		seen := map[string]bool{}
		lastRank := -1
		for stepIndex < len(steps) && steps[stepIndex].Phase == phase {
			step := steps[stepIndex]
			if step.Order != stepIndex+1 {
				return fmt.Errorf("step order %d is not contiguous at index %d", step.Order, stepIndex)
			}
			component, exists := components[step.ComponentID]
			if !exists {
				return fmt.Errorf("step %d names unknown component %q", step.Order, step.ComponentID)
			}
			if seen[step.ComponentID] {
				return fmt.Errorf("component %q appears more than once in phase %d", step.ComponentID, phase)
			}
			seen[step.ComponentID] = true
			if step.ComponentType != component.Type {
				return fmt.Errorf("component %q type mismatch", step.ComponentID)
			}
			rule, exists := rules[component.Type]
			if !exists {
				return fmt.Errorf("phase %d has no sequence rule for component type %q", phase, component.Type)
			}
			if rule.Rank < lastRank {
				return fmt.Errorf("component %q violates the phase %d upgrade sequence", step.ComponentID, phase)
			}
			lastRank = rule.Rank
			if step.FromVersion != versions[component.ID] {
				return fmt.Errorf("component %q fromVersion %q does not match %q", component.ID, step.FromVersion, versions[component.ID])
			}
			target, exists := edge.ComponentTargets[component.Type]
			if !exists || step.ToVersion != target {
				return fmt.Errorf("component %q target %q does not match pinned target %q", component.ID, step.ToVersion, target)
			}
			if step.FromSite != sites[component.ID] {
				return fmt.Errorf("component %q fromSite does not match current placement", component.ID)
			}
			expectedSite := sites[component.ID]
			if component.ID == inventory.ManagementDomain.WitnessComponentID {
				expectedSite = inventory.IndependentWitness
			}
			if step.ToSite != expectedSite {
				return fmt.Errorf("component %q target site %q does not match %q", component.ID, step.ToSite, expectedSite)
			}
			if !reflect.DeepEqual(step.Gates, rule.Gates) {
				return fmt.Errorf("component %q gates %v do not match pinned gates %v", component.ID, step.Gates, rule.Gates)
			}
			versions[component.ID] = step.ToVersion
			sites[component.ID] = step.ToSite
			stepIndex++
		}
		if len(seen) != len(components) {
			return fmt.Errorf("phase %d names %d of %d inventory components", phase, len(seen), len(components))
		}
	}
	if stepIndex != len(steps) {
		return fmt.Errorf("plan contains steps outside the pinned upgrade phases")
	}
	return nil
}
