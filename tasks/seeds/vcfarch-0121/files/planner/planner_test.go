package planner_test

import (
	"encoding/json"
	"os"
	"reflect"
	"sort"
	"testing"

	"vcfplan/planner"
)

func loadJSON[T any](t *testing.T, path string) T {
	t.Helper()
	var value T
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(b, &value); err != nil {
		t.Fatal(err)
	}
	return value
}

func fixtures(t *testing.T) (planner.Inventory, planner.Snapshot) {
	t.Helper()
	return loadJSON[planner.Inventory](t, "../testdata/estate.json"),
		loadJSON[planner.Snapshot](t, "../testdata/compatibility-snapshot.json")
}

func TestBuildValidationTable(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*planner.Inventory, *planner.Snapshot)
		ok     bool
	}{
		{name: "fixture", mutate: func(*planner.Inventory, *planner.Snapshot) {}, ok: true},
		{name: "management domain not immutable", mutate: func(i *planner.Inventory, _ *planner.Snapshot) {
			i.Fleet.ManagementDomain.Immutable = false
		}},
		{name: "unsupported source build", mutate: func(i *planner.Inventory, _ *planner.Snapshot) {
			i.Components[0].Build = "newer-than-pinned"
		}},
		{name: "duplicate component", mutate: func(i *planner.Inventory, _ *planner.Snapshot) {
			i.Components[1].ID = i.Components[0].ID
		}},
		{name: "missing target", mutate: func(_ *planner.Inventory, s *planner.Snapshot) {
			s.Targets = s.Targets[1:]
		}},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			inventory, snapshot := fixtures(t)
			tc.mutate(&inventory, &snapshot)
			plan, err := planner.Build(inventory, snapshot)
			if tc.ok && err != nil {
				t.Fatalf("Build() error = %v", err)
			}
			if !tc.ok && err == nil {
				t.Fatalf("Build() unexpectedly succeeded: %#v", plan)
			}
		})
	}
}

func TestBuildDeterministicAndComplete(t *testing.T) {
	inventory, snapshot := fixtures(t)
	first, err := planner.Build(inventory, snapshot)
	if err != nil {
		t.Fatal(err)
	}

	// Input order is not architecture order. Reversing it must not change the
	// emitted artifact.
	for left, right := 0, len(inventory.Components)-1; left < right; left, right = left+1, right-1 {
		inventory.Components[left], inventory.Components[right] = inventory.Components[right], inventory.Components[left]
	}
	second, err := planner.Build(inventory, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	one, _ := json.Marshal(first)
	two, _ := json.Marshal(second)
	if !reflect.DeepEqual(one, two) {
		t.Fatal("Build output changes when inventory order changes")
	}

	if len(first.Components) != len(inventory.Components) {
		t.Fatalf("got %d planned components, want %d", len(first.Components), len(inventory.Components))
	}
	ids := make([]string, 0, len(first.Components))
	for _, component := range first.Components {
		ids = append(ids, component.ID)
	}
	if !sort.StringsAreSorted(ids) {
		t.Fatalf("components are not deterministically sorted: %v", ids)
	}
	if first.ManagementDomainImpact.Change != "none" ||
		first.ManagementDomainImpact.ManagementDomainID != inventory.Fleet.ManagementDomain.ID {
		t.Fatalf("management-domain impact = %#v", first.ManagementDomainImpact)
	}
	if len(first.Steps) != len(snapshot.Operations) {
		t.Fatalf("got %d steps, want %d", len(first.Steps), len(snapshot.Operations))
	}

	var spec map[string]any
	if err := json.Unmarshal(first.TargetSddcSpec, &spec); err != nil {
		t.Fatalf("targetSddcSpec is not JSON: %v", err)
	}
	if spec["sddcId"] != inventory.DesiredDomain || spec["workflowType"] != "VCF" {
		t.Fatalf("wrong brownfield installer projection: %#v", spec)
	}
	vcenter, ok := spec["vcenterSpec"].(map[string]any)
	if !ok || vcenter["useExistingDeployment"] != true ||
		vcenter["vcenterHostname"] != inventory.Topology.VCenterHostname {
		t.Fatalf("wrong existing vCenter projection: %#v", spec["vcenterSpec"])
	}
}
