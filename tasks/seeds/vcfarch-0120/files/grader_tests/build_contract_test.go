package grader_tests

import (
	"os"
	"testing"

	vcfarch "example.com/vcfarch"
)

func loadBuildFixtures(t *testing.T) (vcfarch.Inventory, vcfarch.CompatibilitySnapshot) {
	t.Helper()
	inventoryFile, err := os.Open(rootPath("fixtures", "estate_inventory.json"))
	if err != nil {
		t.Fatal(err)
	}
	inventory, err := vcfarch.LoadInventory(inventoryFile)
	inventoryFile.Close()
	if err != nil {
		t.Fatal(err)
	}
	snapshotFile, err := os.Open(rootPath("fixtures", "compatibility_snapshot.json"))
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := vcfarch.LoadCompatibility(snapshotFile)
	snapshotFile.Close()
	if err != nil {
		t.Fatal(err)
	}
	return inventory, snapshot
}

func componentRuleIndex(t *testing.T, snapshot vcfarch.CompatibilitySnapshot, id string) int {
	t.Helper()
	for index, rule := range snapshot.Components {
		if rule.ID == id {
			return index
		}
	}
	t.Fatalf("fixture has no component rule %q", id)
	return -1
}

func TestBuildUsesSuppliedInventoryAndSnapshot(t *testing.T) {
	inventory, snapshot := loadBuildFixtures(t)
	inventory.EstateID = "alternate-estate"
	inventory.Site.ID = "alt01"
	inventory.Design.SddcID = "alt01-m01"
	inventory.Design.VCFInstanceName = "alt01-vcf"
	inventory.Design.Hosts[0] = "alt-esx-01"
	inventory.Design.VCenterHostname = "alt-vcenter.example.com"
	inventory.Design.NSXManagers[0] = "alt-nsx.example.com"
	inventory.Components[0].Name = "Alternate replication service"
	inventory.Components[0].Version = "8.6.0"
	vrIndex := componentRuleIndex(t, snapshot, "vr")
	snapshot.Components[vrIndex].UpgradeEdges = append([]vcfarch.UpgradeEdge{{
		From: "8.6.0", To: "8.7.0.5", Action: "UPGRADE",
	}}, snapshot.Components[vrIndex].UpgradeEdges...)

	nsxIndex := componentRuleIndex(t, snapshot, "nsx")
	snapshot.Components[nsxIndex].TargetVersion = "4.2.2"
	snapshot.Components[nsxIndex].UpgradeEdges[0].To = "4.2.2"
	for index := range snapshot.Interoperability {
		pair := &snapshot.Interoperability[index]
		if pair.LeftComponent == "nsx" {
			pair.LeftVersion = "4.2.2"
		}
		if pair.RightComponent == "nsx" {
			pair.RightVersion = "4.2.2"
		}
	}

	artifact, err := vcfarch.Build(inventory, snapshot)
	if err != nil {
		t.Fatalf("Build returned error for supported mutated inputs: %v", err)
	}
	if artifact.SddcID != inventory.Design.SddcID ||
		artifact.VCFInstanceName != inventory.Design.VCFInstanceName ||
		artifact.MigrationPlan.EstateID != inventory.EstateID ||
		artifact.MigrationPlan.SiteID != inventory.Site.ID {
		t.Error("Build did not derive artifact identity from the supplied inventory")
	}
	foundHost, foundManager := false, false
	for _, host := range artifact.HostSpecs {
		foundHost = foundHost || host.Hostname == inventory.Design.Hosts[0]
	}
	for _, manager := range artifact.NSXTSpec.Managers {
		foundManager = foundManager || manager.Hostname == inventory.Design.NSXManagers[0]
	}
	if !foundHost || !foundManager || artifact.VCenterSpec.VCenterHostname != inventory.Design.VCenterHostname {
		t.Error("Build did not derive existing deployment details from the supplied inventory")
	}
	if artifact.NSXTSpec.Version != "4.2.2" {
		t.Errorf("NSX target = %q, want mutated snapshot target", artifact.NSXTSpec.Version)
	}
	var replicationSteps []vcfarch.MigrationStep
	for _, step := range artifact.MigrationPlan.Steps {
		if step.ComponentID == "vr" {
			replicationSteps = append(replicationSteps, step)
		}
	}
	if len(replicationSteps) != 3 || replicationSteps[0].CurrentVersion != "8.6.0" ||
		replicationSteps[0].Component != inventory.Components[0].Name {
		t.Fatalf("Build did not derive the multi-hop component plan from supplied inputs: %+v", replicationSteps)
	}
}

func TestBuildRejectsUnsupportedConstraints(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*vcfarch.Inventory, *vcfarch.CompatibilitySnapshot)
	}{
		{name: "target release mismatch", mutate: func(inventory *vcfarch.Inventory, _ *vcfarch.CompatibilitySnapshot) {
			inventory.TargetRelease = "different-release"
		}},
		{name: "non-single-site snapshot", mutate: func(_ *vcfarch.Inventory, snapshot *vcfarch.CompatibilitySnapshot) {
			snapshot.Architecture.SiteCount = 2
		}},
		{name: "architecture model mismatch", mutate: func(inventory *vcfarch.Inventory, _ *vcfarch.CompatibilitySnapshot) {
			inventory.Design.Model = "STANDARD"
		}},
		{name: "below minimum hosts", mutate: func(inventory *vcfarch.Inventory, _ *vcfarch.CompatibilitySnapshot) {
			inventory.Design.Hosts = inventory.Design.Hosts[:3]
		}},
		{name: "unreachable target", mutate: func(_ *vcfarch.Inventory, snapshot *vcfarch.CompatibilitySnapshot) {
			index := componentRuleIndex(t, *snapshot, "lsr")
			snapshot.Components[index].UpgradeEdges = nil
		}},
		{name: "cyclic precedence", mutate: func(_ *vcfarch.Inventory, snapshot *vcfarch.CompatibilitySnapshot) {
			snapshot.Precedence = append(snapshot.Precedence, vcfarch.PrecedenceRule{Before: "vsan", After: "vr"})
		}},
		{name: "unknown precedence component", mutate: func(_ *vcfarch.Inventory, snapshot *vcfarch.CompatibilitySnapshot) {
			snapshot.Precedence = append(snapshot.Precedence, vcfarch.PrecedenceRule{Before: "missing", After: "vr"})
		}},
		{name: "interoperability version inconsistent with target", mutate: func(_ *vcfarch.Inventory, snapshot *vcfarch.CompatibilitySnapshot) {
			snapshot.Interoperability[0].LeftVersion = "unsupported-vcenter-version"
		}},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			inventory, snapshot := loadBuildFixtures(t)
			test.mutate(&inventory, &snapshot)
			if _, err := vcfarch.Build(inventory, snapshot); err == nil {
				t.Fatal("Build succeeded for unsupported constraints")
			}
		})
	}
}
