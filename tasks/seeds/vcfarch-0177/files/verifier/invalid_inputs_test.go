package verifier_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"vcfmigration/migrationplan"
)

func TestBuildRejectsInvalidInputs(t *testing.T) {
	invBytes, err := os.ReadFile(filepath.Join(projectRoot, "fixtures/estate.json"))
	if err != nil {
		t.Fatal(err)
	}
	snapBytes, err := os.ReadFile(filepath.Join(projectRoot, "spec/compatibility-snapshot.json"))
	if err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		name      string
		inventory func(string) string
		snapshot  func(string) string
	}{
		{
			name: "malformed inventory JSON",
			inventory: func(string) string {
				return "{"
			},
		},
		{
			name: "trailing snapshot JSON",
			snapshot: func(s string) string {
				return s + "\n{}"
			},
		},
		{
			name: "unknown inventory field",
			inventory: func(s string) string {
				return strings.Replace(s, `"inventory_id":`, `"unexpected": true, "inventory_id":`, 1)
			},
		},
		{
			name: "unknown snapshot field",
			snapshot: func(s string) string {
				return strings.Replace(s, `"snapshot_id":`, `"unexpected": true, "snapshot_id":`, 1)
			},
		},
		{
			name: "inventory authority mismatch",
			inventory: func(s string) string {
				return strings.Replace(s, "northstar-observability-estate", "different-estate", 1)
			},
		},
		{
			name: "unsupported snapshot schema",
			snapshot: func(s string) string {
				return strings.Replace(s, `"schema_version": "1.0"`, `"schema_version": "2.0"`, 1)
			},
		},
		{
			name: "management domain mismatch",
			snapshot: func(s string) string {
				return strings.Replace(s, `"management_domain_id": "mgmt-01"`, `"management_domain_id": "other-mgmt"`, 1)
			},
		},
		{
			name: "source version mismatch",
			inventory: func(s string) string {
				return strings.Replace(s, `"version": "8.18.6"`, `"version": "8.18.5"`, 1)
			},
		},
		{
			name: "missing item disposition",
			snapshot: func(s string) string {
				return strings.Replace(s, "        \"ops-dashboard-capacity\": \"carry\",\n", "", 1)
			},
		},
		{
			name: "invalid item disposition",
			snapshot: func(s string) string {
				return strings.Replace(s, `: "carry"`, `: "move"`, 1)
			},
		},
		{
			name: "placement outside workload domain",
			snapshot: func(s string) string {
				return strings.Replace(s, `"domain_id": "wld-observability-01"`, `"domain_id": "mgmt-01"`, 1)
			},
		},
		{
			name: "insufficient workload capacity",
			inventory: func(s string) string {
				return strings.Replace(s, `"vcpu": 192`, `"vcpu": 1`, 1)
			},
		},
		{
			name: "unknown required gate",
			snapshot: func(s string) string {
				return strings.Replace(s, `"id": "management-baseline-clean"`, `"id": "unknown-required-gate"`, 1)
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			invText, snapText := string(invBytes), string(snapBytes)
			if tt.inventory != nil {
				invText = tt.inventory(invText)
			}
			if tt.snapshot != nil {
				snapText = tt.snapshot(snapText)
			}
			dir := t.TempDir()
			invPath := filepath.Join(dir, "inventory.json")
			snapPath := filepath.Join(dir, "snapshot.json")
			if err := os.WriteFile(invPath, []byte(invText), 0o600); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(snapPath, []byte(snapText), 0o600); err != nil {
				t.Fatal(err)
			}
			if _, err := migrationplan.Build(invPath, snapPath); err == nil {
				t.Fatal("Build accepted mismatched or invalid input")
			}
		})
	}
}

func TestBuildRejectsMissingInputs(t *testing.T) {
	validInventory := filepath.Join(projectRoot, "fixtures/estate.json")
	validSnapshot := filepath.Join(projectRoot, "spec/compatibility-snapshot.json")
	tests := []struct {
		name      string
		inventory string
		snapshot  string
	}{
		{"missing inventory", filepath.Join(t.TempDir(), "missing.json"), validSnapshot},
		{"missing snapshot", validInventory, filepath.Join(t.TempDir(), "missing.json")},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if _, err := migrationplan.Build(tt.inventory, tt.snapshot); err == nil {
				t.Fatal("Build accepted a missing input")
			}
		})
	}
}
