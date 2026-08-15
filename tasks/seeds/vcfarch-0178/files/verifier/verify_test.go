package verifier

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestMigrationArtifact(t *testing.T) {
	root := ".."
	if err := VerifyFiles(
		filepath.Join(root, "migration_plan.json"),
		filepath.Join(root, "installer_spec.schema.json"),
		filepath.Join(root, "estate_inventory.json"),
		filepath.Join(root, "compatibility_snapshot.json"),
	); err != nil {
		t.Fatal(err)
	}
}

func TestSchemaValidationRunsBeforeProtectedInputs(t *testing.T) {
	root := ".."
	tests := []struct {
		name     string
		artifact string
	}{
		{name: "wrong root type", artifact: `[]`},
		{name: "missing required fields", artifact: `{}`},
		{name: "malformed JSON", artifact: `{"schema_version":`},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			temp := t.TempDir()
			planPath := filepath.Join(temp, "migration_plan.json")
			if err := os.WriteFile(planPath, []byte(tt.artifact), 0o600); err != nil {
				t.Fatal(err)
			}
			err := VerifyFiles(
				planPath,
				filepath.Join(root, "installer_spec.schema.json"),
				filepath.Join(temp, "missing-inventory.json"),
				filepath.Join(temp, "missing-snapshot.json"),
			)
			if err == nil || !strings.HasPrefix(err.Error(), "schema:") {
				t.Fatalf("expected schema error before protected-input access, got %v", err)
			}
		})
	}
}

func TestVerifierRejectsArchitecturalMutations(t *testing.T) {
	root := ".."
	base, err := os.ReadFile(filepath.Join(root, "migration_plan.json"))
	if err != nil {
		t.Fatal(err)
	}
	tests := []struct {
		name   string
		mutate func(map[string]any)
		want   string
	}{
		{
			name: "undersized operations target",
			mutate: func(doc map[string]any) {
				component := findTargetByProduct(doc, "VCF Operations")
				component["node_count"] = float64(1)
			},
			want: "sizing does not match pinned profile",
		},
		{
			name: "unsupported operations upgrade",
			mutate: func(doc map[string]any) {
				migration := findMigrationBySourceProduct(doc, "VMware Aria Operations")
				migration["strategy"] = "fleet_import_then_upgrade"
			},
			want: "strategy",
		},
		{
			name: "omitted content disposition",
			mutate: func(doc map[string]any) {
				migration := findMigrationBySourceProduct(doc, "VMware Aria Automation")
				content := migration["content"].([]any)
				migration["content"] = content[:len(content)-1]
			},
			want: "content must cover exactly",
		},
		{
			name: "step without exit gate",
			mutate: func(doc map[string]any) {
				step := doc["steps"].([]any)[0].(map[string]any)
				gates := step["gates"].([]any)
				step["gates"] = []any{gates[0], gates[0]}
			},
			want: "entry and exit gates",
		},
		{
			name: "cutover before validation",
			mutate: func(doc map[string]any) {
				migrationID := findMigrationBySourceProduct(doc, "VMware Aria Operations")["id"]
				steps := doc["steps"].([]any)
				var validation, cutover map[string]any
				for _, raw := range steps {
					step := raw.(map[string]any)
					if step["migration_id"] == migrationID && step["operation"] == "validate" {
						validation = step
					}
					if step["migration_id"] == migrationID && step["operation"] == "cutover" {
						cutover = step
					}
				}
				validation["operation"], cutover["operation"] = cutover["operation"], validation["operation"]
			},
			want: "out of supported order",
		},
		{
			name: "operation outside pinned strategy",
			mutate: func(doc map[string]any) {
				migration := findMigrationBySourceProduct(doc, "VMware Aria Operations")
				steps := doc["steps"].([]any)
				id := "verifier-unexpected-operation"
				for stepIDExists(steps, id) {
					id += "-x"
				}
				steps = append(steps, map[string]any{
					"order":        float64(len(steps) + 1),
					"id":           id,
					"migration_id": migration["id"],
					"operation":    "transfer_data",
					"action":       "Run an operation that is absent from the pinned migration strategy.",
					"gates": []any{
						map[string]any{"type": "entry", "condition": "The unrelated operation is ready to begin.", "evidence": "Readiness record."},
						map[string]any{"type": "exit", "condition": "The unrelated operation reports completion.", "evidence": "Completion record."},
					},
				})
				doc["steps"] = steps
			},
			want: "not part of its pinned strategy",
		},
		{
			name: "non Broadcom research host",
			mutate: func(doc map[string]any) {
				source := doc["research"].(map[string]any)["sources"].([]any)[0].(map[string]any)
				source["url"] = "https://example.com/not-a-broadcom-publication"
			},
			want: "not on a Broadcom-published host",
		},
		{
			name: "missing sizing research",
			mutate: func(doc map[string]any) {
				sources := doc["research"].(map[string]any)["sources"].([]any)
				for _, rawSource := range sources {
					source := rawSource.(map[string]any)
					supports := source["supports"].([]any)
					filtered := make([]any, 0, len(supports))
					for _, support := range supports {
						if support != "sizing" {
							filtered = append(filtered, support)
						}
					}
					if len(filtered) == 0 {
						filtered = []any{"placement"}
					}
					source["supports"] = filtered
				}
			},
			want: "required claim \"sizing\"",
		},
		{
			name: "fleet prerequisite omitted from action",
			mutate: func(doc map[string]any) {
				for _, raw := range doc["steps"].([]any) {
					step := raw.(map[string]any)
					if step["operation"] == "prepare_fleet" {
						step["action"] = "Prepare VMware Aria Suite Lifecycle 8.18.0 for Fleet Management without naming the required patch level."
						return
					}
				}
			},
			want: "prerequisite \"Patch 2\"",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			var doc map[string]any
			if err := json.Unmarshal(base, &doc); err != nil {
				t.Fatal(err)
			}
			tt.mutate(doc)
			candidate, err := json.Marshal(doc)
			if err != nil {
				t.Fatal(err)
			}
			planPath := filepath.Join(t.TempDir(), "migration_plan.json")
			if err := os.WriteFile(planPath, candidate, 0o600); err != nil {
				t.Fatal(err)
			}
			err = VerifyFiles(
				planPath,
				filepath.Join(root, "installer_spec.schema.json"),
				filepath.Join(root, "estate_inventory.json"),
				filepath.Join(root, "compatibility_snapshot.json"),
			)
			if err == nil || !strings.Contains(err.Error(), tt.want) {
				t.Fatalf("expected error containing %q, got %v", tt.want, err)
			}
		})
	}
}

func findTargetByProduct(doc map[string]any, product string) map[string]any {
	components := doc["target_architecture"].(map[string]any)["components"].([]any)
	for _, raw := range components {
		component := raw.(map[string]any)
		if component["product"] == product {
			return component
		}
	}
	panic("target product not found: " + product)
}

func findMigrationBySourceProduct(doc map[string]any, product string) map[string]any {
	for _, raw := range doc["migrations"].([]any) {
		migration := raw.(map[string]any)
		if migration["source"].(map[string]any)["product"] == product {
			return migration
		}
	}
	panic("source product not found: " + product)
}

func stepIDExists(steps []any, id string) bool {
	for _, raw := range steps {
		if raw.(map[string]any)["id"] == id {
			return true
		}
	}
	return false
}
