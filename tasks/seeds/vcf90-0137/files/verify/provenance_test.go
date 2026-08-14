// This file is part of the protected verifier. Do not edit it.
package verify

import (
	"encoding/json"
	"os"
	"reflect"
	"sort"
	"testing"

	"vcfopsnetincidents/internal/contractmock"
)

const (
	wantSpecPath = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
	wantCommit   = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
)

type sourceDocument struct {
	Source struct {
		License     string `json:"license"`
		Tag         string `json:"tag"`
		CommitSHA   string `json:"commit_sha"`
		SpecPath    string `json:"spec_path"`
		OpenAPI     string `json:"openapi"`
		InfoVersion string `json:"info_version"`
	} `json:"source"`
	Operations []struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	} `json:"operations"`
}

type contractDocument struct {
	Spec struct {
		Path      string `json:"path"`
		Tag       string `json:"tag"`
		CommitSHA string `json:"commit_sha"`
		Version   string `json:"version"`
	} `json:"spec"`
	BasePath   string `json:"basePath"`
	Operations []struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	} `json:"operations"`
}

func TestPinnedContractProvenanceAndRoutes(t *testing.T) {
	t.Parallel()
	var sources sourceDocument
	readJSON(t, "../docs/official_sources.json", &sources)
	var contract contractDocument
	readJSON(t, "../docs/contract.json", &contract)

	if sources.Source.SpecPath != wantSpecPath || contract.Spec.Path != wantSpecPath {
		t.Errorf("spec path mismatch: sources=%q contract=%q", sources.Source.SpecPath, contract.Spec.Path)
	}
	if sources.Source.CommitSHA != wantCommit || contract.Spec.CommitSHA != wantCommit {
		t.Errorf("commit mismatch: sources=%q contract=%q", sources.Source.CommitSHA, contract.Spec.CommitSHA)
	}
	if sources.Source.Tag != "9.0.0.0" || contract.Spec.Tag != "9.0.0.0" || sources.Source.InfoVersion != "9.0.0.0" || contract.Spec.Version != "9.0.0.0" {
		t.Error("contract is not pinned consistently to VCF 9.0.0.0")
	}
	if sources.Source.License != "Apache-2.0" || sources.Source.OpenAPI != "3.0.1" {
		t.Errorf("source metadata = license %q OpenAPI %q", sources.Source.License, sources.Source.OpenAPI)
	}
	if contract.BasePath != contractmock.BasePath {
		t.Errorf("contract basePath = %q, mock = %q", contract.BasePath, contractmock.BasePath)
	}

	want := []string{"create|POST|/auth/token", "listTroubleshootingIncidents|GET|/gnt/troubleshoot/incidents"}
	fromSources := operationKeys(sources.Operations)
	fromContract := operationKeys(contract.Operations)
	fromMock := make([]string, 0, len(contractmock.ContractOperations()))
	for _, route := range contractmock.ContractOperations() {
		fromMock = append(fromMock, route.OperationID+"|"+route.Method+"|"+route.Path)
	}
	sort.Strings(fromMock)
	if !reflect.DeepEqual(fromSources, want) {
		t.Errorf("official_sources operations = %v, want %v", fromSources, want)
	}
	if !reflect.DeepEqual(fromContract, want) {
		t.Errorf("contract operations = %v, want %v", fromContract, want)
	}
	if !reflect.DeepEqual(fromMock, want) {
		t.Errorf("mock routes = %v, want %v", fromMock, want)
	}
}

func readJSON(t *testing.T, path string, target any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

type operationRecord interface {
	~struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	}
}

func operationKeys[T operationRecord](operations []T) []string {
	data, _ := json.Marshal(operations)
	var normalized []struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	}
	_ = json.Unmarshal(data, &normalized)
	keys := make([]string, 0, len(normalized))
	for _, operation := range normalized {
		keys = append(keys, operation.OperationID+"|"+operation.Method+"|"+operation.Path)
	}
	sort.Strings(keys)
	return keys
}
