package verify

import (
	"errors"
	"os"
	"path/filepath"
	"testing"

	migrationpkg "example.com/northstar/vcf-migration/migration"
)

func TestMigrationPlan(t *testing.T) {
	root := filepath.Join("..", "..")
	schema := mustRead(t, filepath.Join(root, "installer", "migration-plan.schema.json"))
	inventory := mustRead(t, filepath.Join(root, "fixtures", "estate_inventory.json"))
	snapshot := mustRead(t, filepath.Join(root, "fixtures", "compatibility_snapshot.json"))

	if err := Validate(migrationpkg.Document(), schema, inventory, snapshot); err != nil {
		t.Fatalf("migration plan rejected: %v", err)
	}
}

func TestSchemaValidationRunsFirst(t *testing.T) {
	root := filepath.Join("..", "..")
	schema := mustRead(t, filepath.Join(root, "installer", "migration-plan.schema.json"))

	tests := []struct {
		name string
		plan []byte
	}{
		{name: "empty object", plan: []byte(`{}`)},
		{name: "invalid json", plan: []byte(`{"schema_version":`)},
		{name: "wrong top-level type", plan: []byte(`[]`)},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := Validate(tt.plan, schema, []byte(`not inventory json`), []byte(`not snapshot json`))
			var failure *Failure
			if !errors.As(err, &failure) {
				t.Fatalf("Validate() error = %v, want *Failure", err)
			}
			if failure.Stage != "schema" {
				t.Fatalf("failure stage = %q, want schema", failure.Stage)
			}
		})
	}
}

func TestDecodeStrictRejectsTrailingInput(t *testing.T) {
	tests := []string{
		`{} {}`,
		`{} {`,
	}
	for _, document := range tests {
		var destination map[string]any
		if err := decodeStrict([]byte(document), &destination); err == nil {
			t.Fatalf("decodeStrict(%q) unexpectedly succeeded", document)
		}
	}
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return data
}
