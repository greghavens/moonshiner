package vcfarch_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	vcfarch "vcfarch-0122/vcfarch"
)

func fixturePath(parts ...string) string {
	_, source, _, _ := runtime.Caller(0)
	values := append([]string{filepath.Dir(source), ".."}, parts...)
	return filepath.Join(values...)
}

func loadJSON[T any](t *testing.T, path string) T {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var value T
	if err := json.Unmarshal(data, &value); err != nil {
		t.Fatal(err)
	}
	return value
}

func TestBuildUsesPinnedOrderedRules(t *testing.T) {
	inventory := loadJSON[vcfarch.Inventory](t, fixturePath("fixtures", "estate.json"))
	snapshot := loadJSON[vcfarch.CompatibilitySnapshot](t, fixturePath("compatibility", "pinned-compatibility.json"))

	tests := []struct {
		name      string
		index     int
		component string
		strategy  string
		target    string
		gateCount int
	}{
		{"newer vcenter is replaced in parallel", 0, "vc-m01", "parallel-redeploy", "9.1.0.0", 2},
		{"nsx follows the new foundation", 1, "nsx-m01", "parallel-redeploy", "9.1.0.0", 2},
		{"recovery transitions product", 2, "lsr-pair-01", "replace-and-recreate-protection", "VCF Protection and Recovery 9.1.0.0", 2},
		{"storage is migrated before hosts", 3, "vsan-m01", "storage-migrate-and-retire", "9.1.0.0", 2},
		{"hosts are evacuated last", 4, "esx-m01", "evacuate-reimage-and-commission", "9.1.0.0", 2},
	}

	got, err := vcfarch.Build(inventory, snapshot)
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	if len(got.MigrationPlan.Steps) != len(inventory.Components) {
		t.Fatalf("got %d steps for %d components", len(got.MigrationPlan.Steps), len(inventory.Components))
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			step := got.MigrationPlan.Steps[test.index]
			if step.Order != test.index+1 || step.ComponentID != test.component || step.Strategy != test.strategy || step.TargetVersion != test.target {
				t.Fatalf("unexpected step: %+v", step)
			}
			if len(step.Gates) != test.gateCount {
				t.Fatalf("unexpected gates: %v", step.Gates)
			}
		})
	}
}

func TestBuildGreenfieldShape(t *testing.T) {
	inventory := loadJSON[vcfarch.Inventory](t, fixturePath("fixtures", "estate.json"))
	snapshot := loadJSON[vcfarch.CompatibilitySnapshot](t, fixturePath("compatibility", "pinned-compatibility.json"))
	got, err := vcfarch.Build(inventory, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	var spec map[string]any
	if err := json.Unmarshal(got.Greenfield, &spec); err != nil {
		t.Fatal(err)
	}
	tests := []struct {
		field string
		want  any
	}{
		{"workflowType", "VCF"},
		{"version", inventory.TargetBundle},
		{"sddcId", "meridian-m01"},
	}
	for _, test := range tests {
		t.Run(test.field, func(t *testing.T) {
			if spec[test.field] != test.want {
				t.Fatalf("%s = %#v, want %#v", test.field, spec[test.field], test.want)
			}
		})
	}
	for field, want := range map[string]int{"hostSpecs": 4, "networkSpecs": 3, "dvsSpecs": 1} {
		values, ok := spec[field].([]any)
		if !ok || len(values) != want {
			t.Fatalf("%s must contain %d entries, got %#v", field, want, spec[field])
		}
	}
	nsx, ok := spec["nsxtSpec"].(map[string]any)
	if !ok {
		t.Fatalf("nsxtSpec missing: %#v", spec["nsxtSpec"])
	}
	managers, ok := nsx["nsxtManagers"].([]any)
	if !ok || len(managers) != 3 || nsx["useExistingDeployment"] != false {
		t.Fatalf("greenfield NSX shape is wrong: %#v", nsx)
	}
	vc, ok := spec["vcenterSpec"].(map[string]any)
	if !ok || vc["useExistingDeployment"] != false {
		t.Fatalf("vCenter must be newly deployed: %#v", spec["vcenterSpec"])
	}
}
