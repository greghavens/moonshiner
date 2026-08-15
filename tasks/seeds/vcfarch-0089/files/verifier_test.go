package vcfplan

import (
	"encoding/json"
	"errors"
	"testing"
)

func TestSubmittedArchitecture(t *testing.T) {
	if err := Verify("."); err != nil {
		t.Fatalf("submitted migration architecture is invalid: %v", err)
	}
}

func TestInstallerSddcSpecSchemaTable(t *testing.T) {
	openAPI, err := readJSONMap(installerSchemaPath)
	if err != nil {
		t.Fatal(err)
	}
	valid := minimalValidSDDCSpec()
	tests := []struct {
		name    string
		mutate  func(map[string]any)
		wantErr bool
	}{
		{name: "valid", mutate: func(map[string]any) {}, wantErr: false},
		{name: "missing dns", mutate: func(spec map[string]any) { delete(spec, "dnsSpec") }, wantErr: true},
		{name: "invalid short sddc id", mutate: func(spec map[string]any) { spec["sddcId"] = "x" }, wantErr: true},
		{name: "network vlan outside schema maximum", mutate: func(spec map[string]any) {
			spec["networkSpecs"].([]any)[0].(map[string]any)["vlanId"] = 4095
		}, wantErr: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			spec := cloneMap(t, valid)
			test.mutate(spec)
			err := validateOpenAPIComponent(openAPI, "SddcSpec", spec)
			if (err != nil) != test.wantErr {
				t.Fatalf("error = %v, wantErr %v", err, test.wantErr)
			}
		})
	}
}

func TestArtifactSchemaOrderTable(t *testing.T) {
	openAPI, err := readJSONMap(installerSchemaPath)
	if err != nil {
		t.Fatal(err)
	}
	planSchema, err := readJSONMap(planSchemaPath)
	if err != nil {
		t.Fatal(err)
	}
	tests := []struct {
		name      string
		document  map[string]any
		wantStage string
	}{
		{
			name:      "installer schema wins when both contracts fail",
			document:  map[string]any{"targetSddcSpec": map[string]any{"sddcId": "x"}},
			wantStage: "installer-schema",
		},
		{
			name:      "plan schema follows a valid installer spec",
			document:  map[string]any{"targetSddcSpec": minimalValidSDDCSpec()},
			wantStage: "plan-schema",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			err := validateArtifactSchemas(openAPI, planSchema, test.document)
			var stage *StageError
			if !errors.As(err, &stage) || stage.Stage != test.wantStage {
				t.Fatalf("error = %v, want stage %q", err, test.wantStage)
			}
		})
	}
}

func validateArtifactSchemas(openAPI, planSchema, document map[string]any) error {
	if err := validateOpenAPIComponent(openAPI, "SddcSpec", document["targetSddcSpec"]); err != nil {
		return &StageError{Stage: "installer-schema", Err: err}
	}
	if err := ValidateJSONSchema(planSchema, planSchema, document); err != nil {
		return &StageError{Stage: "plan-schema", Err: err}
	}
	return nil
}

func minimalValidSDDCSpec() map[string]any {
	return map[string]any{
		"sddcId":       "chi-m01",
		"workflowType": "VCF",
		"version":      "9.0.1.0",
		"vcenterSpec": map[string]any{
			"vcenterHostname":       "vc01.chi.example.com",
			"rootVcenterPassword":   "Fixture!Vc901",
			"useExistingDeployment": true,
		},
		"dnsSpec": map[string]any{
			"subdomain": "chi.example.com",
		},
		"networkSpecs": []any{
			map[string]any{"networkType": "MANAGEMENT", "vlanId": 1100},
		},
	}
}

func cloneMap(t *testing.T, source map[string]any) map[string]any {
	t.Helper()
	encoded, err := json.Marshal(source)
	if err != nil {
		t.Fatal(err)
	}
	cloned, err := decodeJSONMap(encoded)
	if err != nil {
		t.Fatal(err)
	}
	return cloned
}
