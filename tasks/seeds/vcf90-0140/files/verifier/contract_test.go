package verifier

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

type operationContract struct {
	Method        string `json:"method"`
	Path          string `json:"path"`
	SuccessStatus int    `json:"success_status"`
}

type contractDocument struct {
	APIVersion string `json:"api_version"`
	BasePath   string `json:"base_path"`
	Security   struct {
		Type        string `json:"type"`
		In          string `json:"in"`
		Name        string `json:"name"`
		ValueFormat string `json:"value_format"`
	} `json:"security"`
	Operations map[string]operationContract `json:"operations"`
}

type officialSources struct {
	Tag          string   `json:"tag"`
	CommitSHA    string   `json:"commit_sha"`
	License      string   `json:"license"`
	SpecPath     string   `json:"spec_path"`
	OperationIDs []string `json:"operation_ids"`
}

func TestPinnedOfficialContract(t *testing.T) {
	contractBytes, err := os.ReadFile("../docs/contract.json")
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var contract contractDocument
	if err := json.Unmarshal(contractBytes, &contract); err != nil {
		t.Fatalf("decode contract: %v", err)
	}

	if contract.APIVersion != "9.0.0.0" || contract.BasePath != "/api/ni" {
		t.Fatalf("unexpected API identity: version=%q base_path=%q", contract.APIVersion, contract.BasePath)
	}
	if contract.Security.Type != "apiKey" || contract.Security.In != "header" ||
		contract.Security.Name != "Authorization" || contract.Security.ValueFormat != "NetworkInsight {token}" {
		t.Fatalf("unexpected security contract: %+v", contract.Security)
	}

	wantOperations := map[string]operationContract{
		"updateVcenter": {Method: "PUT", Path: "/data-sources/vcenters/{id}", SuccessStatus: 200},
		"enableVcenter": {Method: "POST", Path: "/data-sources/vcenters/{id}/enable", SuccessStatus: 200},
	}
	if !reflect.DeepEqual(contract.Operations, wantOperations) {
		t.Fatalf("operations differ from pinned contract:\n got: %#v\nwant: %#v", contract.Operations, wantOperations)
	}

	sourcesBytes, err := os.ReadFile("../docs/official_sources.json")
	if err != nil {
		t.Fatalf("read official sources: %v", err)
	}
	var sources officialSources
	if err := json.Unmarshal(sourcesBytes, &sources); err != nil {
		t.Fatalf("decode official sources: %v", err)
	}
	if sources.Tag != "9.0.0.0" || sources.CommitSHA != "85151f6b1bb58f13b6ac0304bfec53904bea085f" ||
		sources.License != "Apache-2.0" || sources.SpecPath != "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml" {
		t.Fatalf("unexpected official source pin: %+v", sources)
	}
	if !reflect.DeepEqual(sources.OperationIDs, []string{"updateVcenter", "enableVcenter"}) {
		t.Fatalf("operation IDs = %v", sources.OperationIDs)
	}
}
