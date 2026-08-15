package verifier

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestVerifierRejectsArchitecturalRegressions(t *testing.T) {
	root := filepath.Join("..", "..")
	artifact := mustRead(t, filepath.Join(root, "architecture.json"))
	openAPI := mustRead(t, filepath.Join(root, "specifications", "vcf-installer", "vcf-installer-openapi.json"))
	planSchema := mustRead(t, filepath.Join(root, "testdata", "migration-plan.schema.json"))
	inventory := mustRead(t, filepath.Join(root, "testdata", "estate.json"))
	snapshot := mustRead(t, filepath.Join(root, "testdata", "compatibility-snapshot.json"))

	var baseline map[string]any
	if err := json.Unmarshal(artifact, &baseline); err != nil {
		t.Fatal(err)
	}
	tests := []struct {
		name       string
		mutate     func(map[string]any)
		wantPrefix string
		wantText   string
	}{
		{
			name: "installer schema is checked first",
			mutate: func(document map[string]any) {
				document["sddcId"] = "x"
				plan(document)["steps"] = []any{}
			},
			wantPrefix: "installer schema:",
		},
		{
			name: "unsupported direct hop",
			mutate: func(document map[string]any) {
				plan(document)["upgradePath"] = []any{"5.1.1", "9.1.0.0"}
			},
			wantText: "upgrade path",
		},
		{
			name: "witness in a data site",
			mutate: func(document map[string]any) {
				topology := plan(document)["targetTopology"].(map[string]any)
				witness := topology["witness"].(map[string]any)
				witness["siteId"] = "chicago-a"
			},
			wantText: "witness",
		},
		{
			name: "missing component step",
			mutate: func(document map[string]any) {
				steps := plan(document)["steps"].([]any)
				plan(document)["steps"] = steps[:len(steps)-1]
			},
			wantText: "names 11 of 12",
		},
		{
			name: "wrong component target",
			mutate: func(document map[string]any) {
				steps := plan(document)["steps"].([]any)
				steps[0].(map[string]any)["toVersion"] = "9.1.0.0"
			},
			wantText: "pinned target",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			document := deepCopy(t, baseline)
			test.mutate(document)
			raw, err := json.Marshal(document)
			if err != nil {
				t.Fatal(err)
			}
			err = VerifyBytes(raw, openAPI, planSchema, inventory, snapshot)
			if err == nil {
				t.Fatal("VerifyBytes() succeeded for invalid architecture")
			}
			if test.wantPrefix != "" && !strings.HasPrefix(err.Error(), test.wantPrefix) {
				t.Fatalf("error %q does not start with %q", err, test.wantPrefix)
			}
			if test.wantText != "" && !strings.Contains(err.Error(), test.wantText) {
				t.Fatalf("error %q does not contain %q", err, test.wantText)
			}
		})
	}
}

func plan(document map[string]any) map[string]any {
	return document["x-migrationPlan"].(map[string]any)
}

func deepCopy(t *testing.T, source map[string]any) map[string]any {
	t.Helper()
	raw, err := json.Marshal(source)
	if err != nil {
		t.Fatal(err)
	}
	var destination map[string]any
	if err := json.Unmarshal(raw, &destination); err != nil {
		t.Fatal(err)
	}
	return destination
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}
