package architecture

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestBuildContract(t *testing.T) {
	tests := []struct {
		name          string
		inventory     string
		compatibility string
		wantErr       bool
	}{
		{
			name:          "protected estate builds",
			inventory:     "../fixtures/estate-inventory.json",
			compatibility: "../fixtures/compatibility-snapshot.json",
		},
		{
			name:          "missing inventory is rejected",
			inventory:     "../fixtures/does-not-exist.json",
			compatibility: "../fixtures/compatibility-snapshot.json",
			wantErr:       true,
		},
		{
			name:          "missing compatibility snapshot is rejected",
			inventory:     "../fixtures/estate-inventory.json",
			compatibility: "../fixtures/does-not-exist.json",
			wantErr:       true,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			artifact, err := Build(test.inventory, test.compatibility)
			if test.wantErr {
				if err == nil {
					t.Fatal("Build returned no error")
				}
				return
			}
			if err != nil {
				t.Fatalf("Build: %v", err)
			}
			if artifact.SddcID != "chi-apps-01" {
				t.Fatalf("SddcID = %q", artifact.SddcID)
			}
			if artifact.Management.Disposition != "unchanged" {
				t.Fatalf("management disposition = %q", artifact.Management.Disposition)
			}
			if len(artifact.Management.Components) != 4 {
				t.Fatalf("management component count = %d", len(artifact.Management.Components))
			}
			if len(artifact.MigrationPlan.FinalComponents) != 4 {
				t.Fatalf("workload component count = %d", len(artifact.MigrationPlan.FinalComponents))
			}
			if len(artifact.MigrationPlan.Steps) == 0 {
				t.Fatal("migration plan has no steps")
			}
			encoded, err := os.ReadFile("../out/architecture.json")
			if err != nil {
				t.Fatalf("read generated architecture: %v", err)
			}
			var checkedIn Artifact
			if err := json.Unmarshal(encoded, &checkedIn); err != nil {
				t.Fatalf("decode generated architecture: %v", err)
			}
			builtJSON, err := json.Marshal(artifact)
			if err != nil {
				t.Fatalf("encode Build result: %v", err)
			}
			checkedJSON, err := json.Marshal(checkedIn)
			if err != nil {
				t.Fatalf("encode checked-in architecture: %v", err)
			}
			if !bytes.Equal(builtJSON, checkedJSON) {
				t.Fatal("Build result does not match out/architecture.json")
			}
		})
	}
}

func TestBuildUsesInventoryAndCompatibilityContents(t *testing.T) {
	t.Run("inventory projection changes with input", func(t *testing.T) {
		var inventory map[string]any
		decodeFixture(t, "../fixtures/estate-inventory.json", &inventory)
		inventory["fleetId"] = "alternate-fleet"
		workload := inventory["workloadDomain"].(map[string]any)
		workload["domainId"] = "chi-apps-02"
		inputs := workload["installerInputs"].(map[string]any)
		inputs["vcenterHostname"] = "apps-vc02.lab.example.com"

		inventoryPath := writeJSONFixture(t, "inventory.json", inventory)
		artifact, err := Build(inventoryPath, "../fixtures/compatibility-snapshot.json")
		if err != nil {
			t.Fatalf("Build mutated inventory: %v", err)
		}
		if artifact.FleetID != "alternate-fleet" || artifact.SddcID != "chi-apps-02" {
			t.Fatalf("Build did not project mutated inventory identity: fleet=%q sddc=%q", artifact.FleetID, artifact.SddcID)
		}
		if got := artifact.VcenterSpec["vcenterHostname"]; got != "apps-vc02.lab.example.com" {
			t.Fatalf("vcenter hostname = %v", got)
		}
	})

	t.Run("required gates change with snapshot", func(t *testing.T) {
		var snapshot map[string]any
		decodeFixture(t, "../fixtures/compatibility-snapshot.json", &snapshot)
		changed := false
		for _, raw := range snapshot["transitions"].([]any) {
			rule := raw.(map[string]any)
			if rule["action"] == "UPGRADE_NSX" {
				rule["requiredGates"] = append(rule["requiredGates"].([]any), "existing-vcenter-nsx-source-compatible")
				changed = true
			}
		}
		if !changed {
			t.Fatal("protected snapshot has no UPGRADE_NSX transition")
		}

		snapshotPath := writeJSONFixture(t, "compatibility.json", snapshot)
		artifact, err := Build("../fixtures/estate-inventory.json", snapshotPath)
		if err != nil {
			t.Fatalf("Build mutated compatibility snapshot: %v", err)
		}
		for _, step := range artifact.MigrationPlan.Steps {
			if step.Action == "UPGRADE_NSX" {
				if !containsString(step.Gates, "existing-vcenter-nsx-source-compatible") {
					t.Fatal("UPGRADE_NSX step omitted gate added to compatibility snapshot")
				}
				return
			}
		}
		t.Fatal("migration plan omitted UPGRADE_NSX")
	})
}

func decodeFixture(t *testing.T, path string, destination any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, destination); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func writeJSONFixture(t *testing.T, name string, value any) string {
	t.Helper()
	data, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("encode %s: %v", name, err)
	}
	path := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatalf("write %s: %v", path, err)
	}
	return path
}

func containsString(values []string, wanted string) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}
