package schema_test

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"testing"

	"vcfmigration/verifier/schemavalidator"
)

const projectRoot = "../.."

func readAnyJSON(t *testing.T, path string) any {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(projectRoot, path))
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
	var trailing any
	if err := dec.Decode(&trailing); err == nil {
		t.Fatalf("%s has trailing JSON data", path)
	} else if !errors.Is(err, io.EOF) {
		t.Fatalf("decode trailing data in %s: %v", path, err)
	}
	return v
}

// This package deliberately has no dependency on migrationplan, so candidate
// compilation and semantic checks cannot run before installer-schema validation.
func TestArtifactMatchesInstallerSchema(t *testing.T) {
	schema := readAnyJSON(t, "spec/installer.schema.json")
	artifact := readAnyJSON(t, "migration-plan.json")
	if err := schemavalidator.Validate(schema, artifact); err != nil {
		t.Fatalf("migration-plan.json does not satisfy installer schema: %v", err)
	}
}
