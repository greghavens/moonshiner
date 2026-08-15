package architecture

import (
	"os"
	"testing"
)

func TestDecodeInputs(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name     string
		path     string
		wantRead bool
	}{
		{name: "estate fixture", path: "../fixtures/estate.json", wantRead: true},
		{name: "compatibility snapshot", path: "../compatibility/vcf-9.1.0.0-snapshot.json", wantRead: true},
	}

	contents := make([][]byte, len(tests))
	for i, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got, err := os.ReadFile(test.path)
			if test.wantRead && err != nil {
				t.Fatalf("read %s: %v", test.path, err)
			}
			contents[i] = got
		})
	}

	if _, _, err := DecodeInputs(contents[0], contents[1]); err != nil {
		t.Fatalf("DecodeInputs() error = %v", err)
	}
}

func TestBuildersReturnDeliverables(t *testing.T) {
	inventory, snapshot := loadProtectedInputs(t)

	tests := []struct {
		name  string
		build func() error
	}{
		{
			name: "greenfield SddcSpec",
			build: func() error {
				_, err := GreenfieldSddc(snapshot)
				return err
			},
		},
		{
			name: "brownfield migration plan",
			build: func() error {
				_, err := BrownfieldPlan(inventory, snapshot)
				return err
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if err := test.build(); err != nil {
				t.Fatalf("builder failed: %v", err)
			}
		})
	}
}

func TestBuildersHonorInputs(t *testing.T) {
	tests := []struct {
		name    string
		mutate  func(*Inventory, *Snapshot)
		wantErr bool
		check   func(*testing.T, map[string]any, MigrationPlan)
	}{
		{
			name: "fleet identity comes from snapshot",
			mutate: func(_ *Inventory, snapshot *Snapshot) {
				snapshot.Fleet.Name = "alternate-fleet"
			},
			check: func(t *testing.T, _ map[string]any, plan MigrationPlan) {
				if plan.TargetFleet.Name != "alternate-fleet" {
					t.Fatalf("target fleet = %q", plan.TargetFleet.Name)
				}
				for _, step := range plan.Steps {
					if step.Target.Fleet != "alternate-fleet" {
						t.Fatalf("step %q retained hard-coded fleet %q", step.ComponentID, step.Target.Fleet)
					}
				}
			},
		},
		{
			name: "host and service sizing comes from snapshot",
			mutate: func(_ *Inventory, snapshot *Snapshot) {
				snapshot.Greenfield.HostCount = 5
				snapshot.Greenfield.Operations.NodeCount = 2
				snapshot.Greenfield.Operations.Size = "medium"
				snapshot.ServiceSizing[0].NodeCount = 2
				snapshot.ServiceSizing[0].Size = "medium"
			},
			check: func(t *testing.T, sddc map[string]any, plan MigrationPlan) {
				if got := len(sddc["hostSpecs"].([]any)); got != 5 {
					t.Fatalf("host count = %d, want 5", got)
				}
				operations := sddc["vcfOperationsSpec"].(map[string]any)
				if got := len(operations["nodes"].([]any)); got != 2 || operations["applianceSize"] != "medium" {
					t.Fatalf("greenfield Operations sizing = %d/%v", got, operations["applianceSize"])
				}
				if plan.Services[0].NodeCount != 2 || plan.Services[0].Size != "medium" {
					t.Fatalf("plan Operations sizing = %+v", plan.Services[0])
				}
			},
		},
		{
			name: "scope target comes from snapshot",
			mutate: func(_ *Inventory, snapshot *Snapshot) {
				snapshot.ScopeTargets["recovery"] = snapshot.Fleet.ManagementDomain
			},
			check: func(t *testing.T, _ map[string]any, plan MigrationPlan) {
				for _, step := range plan.Steps {
					if step.ComponentType != "live-site-recovery" && step.ComponentType != "vsphere-replication" {
						continue
					}
					if step.Target.Domain != "sfo01-m01" || step.Target.Instance != "sfo01" {
						t.Fatalf("recovery target = %+v, want management scope target", step.Target)
					}
				}
			},
		},
		{
			name: "unsupported inventory version fails closed",
			mutate: func(inventory *Inventory, _ *Snapshot) {
				inventory.Components[0].Version = "unlisted-version"
			},
			wantErr: true,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			inventory, snapshot := loadProtectedInputs(t)
			test.mutate(&inventory, &snapshot)
			sddc, sddcErr := GreenfieldSddc(snapshot)
			plan, planErr := BrownfieldPlan(inventory, snapshot)
			if test.wantErr {
				if planErr == nil {
					t.Fatal("BrownfieldPlan() unexpectedly accepted an unlisted path")
				}
				return
			}
			if sddcErr != nil || planErr != nil {
				t.Fatalf("builders returned errors: greenfield=%v brownfield=%v", sddcErr, planErr)
			}
			test.check(t, sddc, plan)
		})
	}
}

func loadProtectedInputs(t *testing.T) (Inventory, Snapshot) {
	t.Helper()
	inventoryJSON, err := os.ReadFile("../fixtures/estate.json")
	if err != nil {
		t.Fatal(err)
	}
	snapshotJSON, err := os.ReadFile("../compatibility/vcf-9.1.0.0-snapshot.json")
	if err != nil {
		t.Fatal(err)
	}
	inventory, snapshot, err := DecodeInputs(inventoryJSON, snapshotJSON)
	if err != nil {
		t.Fatal(err)
	}
	return inventory, snapshot
}
