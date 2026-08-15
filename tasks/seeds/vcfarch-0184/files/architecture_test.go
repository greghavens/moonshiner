// Protected acceptance tests. Verification is deliberately offline: it reads
// only architecture.json and the pinned files under testdata/.
package vcfarch_test

import (
	"bytes"
	"encoding/json"
	"io"
	"os"
	"testing"

	arch "vcfarch"
)

func openFixture(t *testing.T, name string) *os.File {
	t.Helper()
	f, err := os.Open(name)
	if err != nil {
		t.Fatalf("open %s: %v", name, err)
	}
	t.Cleanup(func() { _ = f.Close() })
	return f
}

func loadAll(t *testing.T) (arch.Architecture, arch.EstateInventory, arch.CompatibilitySnapshot) {
	t.Helper()
	plan, err := arch.LoadArchitecture(openFixture(t, "architecture.json"))
	if err != nil {
		t.Fatalf("load architecture.json: %v", err)
	}
	inventory, err := arch.LoadInventory(openFixture(t, "testdata/estate_inventory.json"))
	if err != nil {
		t.Fatalf("load estate inventory: %v", err)
	}
	snapshot, err := arch.LoadCompatibilitySnapshot(openFixture(t, "testdata/compatibility_snapshot.json"))
	if err != nil {
		t.Fatalf("load compatibility snapshot: %v", err)
	}
	return plan, inventory, snapshot
}

func mutateJSONObject(t *testing.T, data []byte, mutate func(map[string]any)) []byte {
	t.Helper()
	var object map[string]any
	if err := json.Unmarshal(data, &object); err != nil {
		t.Fatalf("decode JSON for mutation: %v", err)
	}
	mutate(object)
	mutated, err := json.Marshal(object)
	if err != nil {
		t.Fatalf("encode mutated JSON: %v", err)
	}
	return mutated
}

func TestArchitectureArtifact(t *testing.T) {
	plan, inventory, snapshot := loadAll(t)
	if err := arch.Validate(plan, inventory, snapshot); err != nil {
		t.Fatalf("architecture is invalid: %v", err)
	}
}

func TestLoadersAreStrict(t *testing.T) {
	architecture, err := os.ReadFile("architecture.json")
	if err != nil {
		t.Fatal(err)
	}
	inventory, err := os.ReadFile("testdata/estate_inventory.json")
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := os.ReadFile("testdata/compatibility_snapshot.json")
	if err != nil {
		t.Fatal(err)
	}
	architectureUnknown := mutateJSONObject(t, architecture, func(object map[string]any) {
		object["unexpected"] = true
	})
	architectureResearchUnknown := mutateJSONObject(t, architecture, func(object map[string]any) {
		research, ok := object["research"].([]any)
		if !ok || len(research) == 0 {
			t.Fatal("architecture research must contain an object")
		}
		source, ok := research[0].(map[string]any)
		if !ok {
			t.Fatal("architecture research entry must be an object")
		}
		source["unexpected"] = true
	})
	inventoryUnknown := mutateJSONObject(t, inventory, func(object map[string]any) {
		object["unexpected"] = true
	})
	snapshotUnknown := mutateJSONObject(t, snapshot, func(object map[string]any) {
		object["unexpected"] = true
	})

	tests := []struct {
		name string
		data []byte
		load func(io.Reader) error
	}{
		{
			name: "architecture unknown field",
			data: architectureUnknown,
			load: func(r io.Reader) error { _, err := arch.LoadArchitecture(r); return err },
		},
		{
			name: "architecture trailing JSON",
			data: append(append([]byte(nil), architecture...), []byte(` {}`)...),
			load: func(r io.Reader) error { _, err := arch.LoadArchitecture(r); return err },
		},
		{
			name: "architecture nested research unknown field",
			data: architectureResearchUnknown,
			load: func(r io.Reader) error { _, err := arch.LoadArchitecture(r); return err },
		},
		{
			name: "inventory unknown field",
			data: inventoryUnknown,
			load: func(r io.Reader) error { _, err := arch.LoadInventory(r); return err },
		},
		{
			name: "snapshot unknown field",
			data: snapshotUnknown,
			load: func(r io.Reader) error { _, err := arch.LoadCompatibilitySnapshot(r); return err },
		},
		{
			name: "snapshot trailing JSON",
			data: append(append([]byte(nil), snapshot...), []byte(` null`)...),
			load: func(r io.Reader) error { _, err := arch.LoadCompatibilitySnapshot(r); return err },
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if err := tt.load(bytes.NewReader(tt.data)); err == nil {
				t.Fatal("loader accepted invalid input")
			}
		})
	}
}

func TestValidationRejectsContradictions(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(*arch.Architecture)
	}{
		{
			name:   "research omitted",
			mutate: func(p *arch.Architecture) { p.Research = nil },
		},
		{
			name:   "research title empty",
			mutate: func(p *arch.Architecture) { p.Research[0].Title = " " },
		},
		{
			name:   "research URL is not an official source",
			mutate: func(p *arch.Architecture) { p.Research[0].URL = "https://example.com/article" },
		},
		{
			name:   "research consultation date invalid",
			mutate: func(p *arch.Architecture) { p.Research[0].ConsultedOn = "soon" },
		},
		{
			name:   "research decision empty",
			mutate: func(p *arch.Architecture) { p.Research[0].UsedFor[0] = "" },
		},
		{
			name:   "wrong pinned snapshot",
			mutate: func(p *arch.Architecture) { p.CompatibilitySnapshot = "moving-live-matrix" },
		},
		{
			name:   "host count contradicts failures to tolerate",
			mutate: func(p *arch.Architecture) { p.Placement.Sites[0].Hosts = 4 },
		},
		{
			name:   "resilience weakened to fit host count",
			mutate: func(p *arch.Architecture) { p.Placement.SFTT = 1 },
		},
		{
			name:   "witness at data site",
			mutate: func(p *arch.Architecture) { p.Placement.Witness.Site = p.Placement.Sites[0].ID },
		},
		{
			name:   "component placed at witness",
			mutate: func(p *arch.Architecture) { p.Placement.Components[0].Nodes[0].Site = p.Placement.Witness.Site },
		},
		{
			name:   "component undersized",
			mutate: func(p *arch.Architecture) { p.Placement.Components[0].Nodes[0].Count-- },
		},
		{
			name:   "wrong demand profile",
			mutate: func(p *arch.Architecture) { p.Placement.Components[1].SizeProfile = "small" },
		},
		{
			name:   "anti affinity omitted",
			mutate: func(p *arch.Architecture) { p.Placement.Components[2].AntiAffinity = false },
		},
		{
			name:   "source version changed",
			mutate: func(p *arch.Architecture) { p.Steps[0].Source.Version = "8.18.5" },
		},
		{
			name:   "support boundary changed",
			mutate: func(p *arch.Architecture) { p.Steps[1].Source.SupportEnds = "2028-01-01" },
		},
		{
			name:   "unsupported migration method",
			mutate: func(p *arch.Architecture) { p.Steps[2].MigrationMethod = "in-place-upgrade" },
		},
		{
			name:   "version hop omitted",
			mutate: func(p *arch.Architecture) { p.Steps[1].VersionPath = p.Steps[1].VersionPath[:2] },
		},
		{
			name:   "step order is not increasing",
			mutate: func(p *arch.Architecture) { p.Steps[2].Order = p.Steps[1].Order },
		},
		{
			name:   "dependency omitted",
			mutate: func(p *arch.Architecture) { p.Steps[1].DependsOn = nil },
		},
		{
			name:   "gate omitted",
			mutate: func(p *arch.Architecture) { p.Steps[0].Gates = p.Steps[0].Gates[:len(p.Steps[0].Gates)-1] },
		},
		{
			name:   "gate criterion empty",
			mutate: func(p *arch.Architecture) { p.Steps[0].Gates[0].Criterion = "" },
		},
		{
			name:   "content omitted",
			mutate: func(p *arch.Architecture) { p.Steps[0].CarryForward = p.Steps[0].CarryForward[1:] },
		},
		{
			name: "content duplicated",
			mutate: func(p *arch.Architecture) {
				p.Steps[0].CarryForward = append(p.Steps[0].CarryForward, p.Steps[0].CarryForward[0])
			},
		},
		{
			name: "content invented",
			mutate: func(p *arch.Architecture) {
				p.Steps[0].CarryForward = append(p.Steps[0].CarryForward, arch.CarryDecision{ContentID: "not-in-inventory", Method: "export-import"})
			},
		},
		{
			name:   "content method incompatible",
			mutate: func(p *arch.Architecture) { p.Steps[2].CarryForward[0].Method = "preserved-by-upgrade" },
		},
		{
			name:   "abandonment unexplained",
			mutate: func(p *arch.Architecture) { p.Steps[0].Abandon[0].Reason = "  " },
		},
		{
			name:   "product step omitted",
			mutate: func(p *arch.Architecture) { p.Steps = p.Steps[:2] },
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			plan, inventory, snapshot := loadAll(t)
			tt.mutate(&plan)
			if err := arch.Validate(plan, inventory, snapshot); err == nil {
				t.Fatal("Validate accepted a contradictory architecture")
			}
		})
	}
}

func TestSchemaFixtureIsPinnedAndMachineReadable(t *testing.T) {
	data, err := os.ReadFile("testdata/architecture_schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var schema struct {
		ID                   string         `json:"$id"`
		AdditionalProperties bool           `json:"additionalProperties"`
		Properties           map[string]any `json:"properties"`
	}
	if err := json.Unmarshal(data, &schema); err != nil {
		t.Fatalf("schema is not JSON: %v", err)
	}
	if schema.ID != "urn:moonshiner:vcfarch-0184:architecture:1.0" {
		t.Fatalf("unexpected schema id %q", schema.ID)
	}
	if schema.AdditionalProperties {
		t.Fatal("top-level schema must reject additional properties")
	}
	for _, field := range []string{"schema_version", "estate_id", "compatibility_snapshot", "design_as_of", "target_platform", "research", "placement", "steps"} {
		if _, ok := schema.Properties[field]; !ok {
			t.Fatalf("schema is missing %s", field)
		}
	}
}
