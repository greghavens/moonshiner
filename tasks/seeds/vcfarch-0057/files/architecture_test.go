package vcfarch

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sync"
	"testing"
)

const (
	scenarioFixture = "fixtures/greenfield.json"
	estateFixture   = "fixtures/estate.json"
	snapshotFixture = "compatibility/vcf-9.0.0-snapshot.json"
)

func buildObject(t *testing.T, scenario, estate, snapshot string) map[string]any {
	t.Helper()
	architecture, err := Build(scenario, estate, snapshot)
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	encoded, err := json.Marshal(architecture)
	if err != nil {
		t.Fatalf("marshal Build result: %v", err)
	}
	var object map[string]any
	if err := json.Unmarshal(encoded, &object); err != nil {
		t.Fatalf("decode Build result: %v", err)
	}
	return object
}

func nested(object map[string]any, path ...string) any {
	var current any = object
	for _, key := range path {
		mapping, ok := current.(map[string]any)
		if !ok {
			return nil
		}
		current = mapping[key]
	}
	return current
}

func readFixtureObject(t *testing.T, path string) map[string]any {
	t.Helper()
	contents, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var object map[string]any
	if err := json.Unmarshal(contents, &object); err != nil {
		t.Fatal(err)
	}
	return object
}

func writeFixtureObject(t *testing.T, dir, name string, object map[string]any) string {
	t.Helper()
	contents, err := json.Marshal(object)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, contents, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestBuildMatchesArtifactAndContract(t *testing.T) {
	got := buildObject(t, scenarioFixture, estateFixture, snapshotFixture)
	wantBytes, err := os.ReadFile("architecture.json")
	if err != nil {
		t.Fatal(err)
	}
	var want map[string]any
	if err := json.Unmarshal(wantBytes, &want); err != nil {
		t.Fatalf("decode architecture.json: %v", err)
	}
	if !reflect.DeepEqual(got, want) {
		gotJSON, _ := json.MarshalIndent(got, "", "  ")
		t.Fatalf("Build result differs from architecture.json\ngot:\n%s", gotJSON)
	}

	checks := []struct {
		name string
		got  any
		want any
	}{
		{"schema version", got["schemaVersion"], float64(1)},
		{"target release", nested(got, "greenfield", "sddcSpec", "version"), "9.0.0.0"},
		{"stretched topology", nested(got, "greenfield", "topology", "mode"), "stretched-management-domain"},
		{"site-loss capacity", nested(got, "greenfield", "capacity", "meetsDataSiteFailureRequirement"), true},
		{"migration target", nested(got, "existingEstate", "migrationPlan", "targetRelease"), "9.0.0.0"},
	}
	for _, tc := range checks {
		t.Run(tc.name, func(t *testing.T) {
			if !reflect.DeepEqual(tc.got, tc.want) {
				t.Fatalf("got %#v, want %#v", tc.got, tc.want)
			}
		})
	}

	steps := nested(got, "existingEstate", "migrationPlan", "steps").([]any)
	if len(steps) != 14 {
		t.Fatalf("got %d migration steps, want 14", len(steps))
	}
	nsx := steps[6].(map[string]any)
	if nested(nsx, "convergence", "disposition") != "skip-newer-current" {
		t.Fatalf("NSX convergence disposition = %#v", nested(nsx, "convergence", "disposition"))
	}
}

func TestBuildRejectsInvalidInputs(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(t *testing.T, dir string) (string, string, string)
	}{
		{
			name: "missing scenario",
			mutate: func(t *testing.T, dir string) (string, string, string) {
				return filepath.Join(dir, "missing.json"), estateFixture, snapshotFixture
			},
		},
		{
			name: "malformed estate",
			mutate: func(t *testing.T, dir string) (string, string, string) {
				path := filepath.Join(dir, "estate.json")
				if err := os.WriteFile(path, []byte("{"), 0o600); err != nil {
					t.Fatal(err)
				}
				return scenarioFixture, path, snapshotFixture
			},
		},
		{
			name: "release mismatch",
			mutate: func(t *testing.T, dir string) (string, string, string) {
				contents, err := os.ReadFile(scenarioFixture)
				if err != nil {
					t.Fatal(err)
				}
				var scenario map[string]any
				if err := json.Unmarshal(contents, &scenario); err != nil {
					t.Fatal(err)
				}
				scenario["targetRelease"] = "9.9.9.9"
				changed, _ := json.Marshal(scenario)
				path := filepath.Join(dir, "scenario.json")
				if err := os.WriteFile(path, changed, 0o600); err != nil {
					t.Fatal(err)
				}
				return path, estateFixture, snapshotFixture
			},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			dir := t.TempDir()
			scenario, estate, snapshot := tc.mutate(t, dir)
			if _, err := Build(scenario, estate, snapshot); err == nil {
				t.Fatal("Build returned nil error")
			}
		})
	}
}

func TestBuildDerivesResultFromSuppliedInputs(t *testing.T) {
	dir := t.TempDir()
	scenario := readFixtureObject(t, scenarioFixture)
	scenario["sddcId"] = "derived-sddc"
	scenario["vcfInstanceName"] = "derived-vcf"
	scenario["infrastructure"].(map[string]any)["vcenterHostname"] = "derived-vcenter.infra.example.com"
	scenario["hosts"].([]any)[0].(map[string]any)["physicalCores"] = float64(72)

	estate := readFixtureObject(t, estateFixture)
	component := estate["components"].([]any)[0].(map[string]any)
	component["name"] = "Derived Lifecycle Manager"
	component["version"] = "8.18.1"

	snapshot := readFixtureObject(t, snapshotFixture)
	snapshot["greenfield"].(map[string]any)["storagePolicy"] = "derived-dual-site-policy"
	for _, raw := range snapshot["billOfMaterials"].([]any) {
		item := raw.(map[string]any)
		if item["id"] == "vsan-witness-esa" {
			item["build"] = "derived-witness-build"
		}
	}
	firstPath := snapshot["migrationPaths"].([]any)[0].(map[string]any)
	firstPath["to"] = "8.18 Derived Patch"
	firstPath["method"] = "derived-patch-method"
	firstPath["gates"] = []any{"derived-health-gate"}

	got := buildObject(t,
		writeFixtureObject(t, dir, "scenario.json", scenario),
		writeFixtureObject(t, dir, "estate.json", estate),
		writeFixtureObject(t, dir, "snapshot.json", snapshot),
	)

	checks := []struct {
		name string
		got  any
		want any
	}{
		{"sddc id", nested(got, "greenfield", "sddcSpec", "sddcId"), "derived-sddc"},
		{"instance name", nested(got, "greenfield", "sddcSpec", "vcfInstanceName"), "derived-vcf"},
		{"vcenter hostname", nested(got, "greenfield", "sddcSpec", "vcenterSpec", "vcenterHostname"), "derived-vcenter.infra.example.com"},
		{"storage policy", nested(got, "greenfield", "topology", "storagePolicy"), "derived-dual-site-policy"},
		{"witness build", nested(got, "greenfield", "topology", "witness", "build"), "derived-witness-build"},
	}
	for _, tc := range checks {
		t.Run(tc.name, func(t *testing.T) {
			if !reflect.DeepEqual(tc.got, tc.want) {
				t.Fatalf("got %#v, want %#v", tc.got, tc.want)
			}
		})
	}

	capacities := nested(got, "greenfield", "capacity", "availableAfterDataSiteFailure").([]any)
	if cores := capacities[0].(map[string]any)["physicalCores"]; cores != float64(264) {
		t.Fatalf("derived first-site cores = %#v, want 264", cores)
	}
	steps := nested(got, "existingEstate", "migrationPlan", "steps").([]any)
	first := steps[0].(map[string]any)
	for key, want := range map[string]any{
		"name":           "Derived Lifecycle Manager",
		"currentVersion": "8.18.1",
		"targetVersion":  "8.18 Derived Patch",
		"method":         "derived-patch-method",
	} {
		if got := first[key]; got != want {
			t.Fatalf("derived first step %s = %#v, want %#v", key, got, want)
		}
	}
	if !reflect.DeepEqual(first["gates"], []any{"derived-health-gate"}) {
		t.Fatalf("derived first-step gates = %#v", first["gates"])
	}
}

func TestBuildConcurrent(t *testing.T) {
	const callers = 16
	var wg sync.WaitGroup
	errors := make(chan error, callers)
	for i := 0; i < callers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := Build(scenarioFixture, estateFixture, snapshotFixture)
			errors <- err
		}()
	}
	wg.Wait()
	close(errors)
	for err := range errors {
		if err != nil {
			t.Fatalf("concurrent Build: %v", err)
		}
	}
}
