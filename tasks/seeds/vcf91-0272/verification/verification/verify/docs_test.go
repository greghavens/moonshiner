package verify

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"

	"vcfops/internal/opsmock"
)

// Facts pinned to the VCF Operations OpenAPI specification as published at the
// "VCF API Specs 9.1 release readiness" revision of vmware/vcf-api-specs.
const (
	wantRepository = "vmware/vcf-api-specs"
	wantSpecPath   = "specifications/vcf-operations/vcf-operations-openapi.json"
	wantCommit     = "c3f3b52c845dd967cabbc21680e893292077d5ba"
	wantLicense    = "Apache-2.0"
	wantAPITitle   = "VMware Cloud Foundation Operations API"
	wantAPIVersion = "9.1.0.0"
	wantBasePath   = "/suite-api"
)

var wantOperationIDs = []string{"acquireToken", "createAdapterInstance", "testConnection"}

var wantCreateOptionalFields = []string{
	"collectorGroupId",
	"collectorId",
	"credential",
	"description",
	"monitoringInterval",
	"monitoringIntervalSeconds",
	"physicalDatacenterId",
	"resourceIdentifiers",
}

type contractSource struct {
	Repository string `json:"repository"`
	SpecPath   string `json:"specPath"`
	Commit     string `json:"commit"`
	License    string `json:"license"`
	APITitle   string `json:"apiTitle"`
	APIVersion string `json:"apiVersion"`
	BasePath   string `json:"basePath"`
}

type contractQueryParam struct {
	Name     string `json:"name"`
	Type     string `json:"type"`
	Required bool   `json:"required"`
	Default  any    `json:"default"`
}

type contractOperation struct {
	OperationID           string               `json:"operationId"`
	Method                string               `json:"method"`
	Path                  string               `json:"path"`
	RequestSchema         string               `json:"requestSchema"`
	RequiredRequestFields []string             `json:"requiredRequestFields"`
	OptionalRequestFields []string             `json:"optionalRequestFields"`
	SuccessStatus         int                  `json:"successStatus"`
	ResponseSchema        string               `json:"responseSchema"`
	QueryParameters       []contractQueryParam `json:"queryParameters"`
	RequiresAuth          bool                 `json:"requiresAuth"`
}

type contractDoc struct {
	Source     contractSource      `json:"source"`
	Operations []contractOperation `json:"operations"`
}

type officialSource struct {
	Repository   string   `json:"repository"`
	URL          string   `json:"url"`
	Path         string   `json:"path"`
	Commit       string   `json:"commit"`
	License      string   `json:"license"`
	Title        string   `json:"title"`
	OperationIDs []string `json:"operationIds"`
}

type officialSourcesDoc struct {
	Sources []officialSource `json:"sources"`
}

func repoRoot(t *testing.T) string {
	t.Helper()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	return filepath.Join(wd, "..", "..")
}

func readJSON(t *testing.T, rel string, into any) {
	t.Helper()
	path := filepath.Join(repoRoot(t), rel)
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("%s: %v", rel, err)
	}
	if err := json.Unmarshal(raw, into); err != nil {
		t.Fatalf("%s: not valid JSON for the required shape: %v", rel, err)
	}
}

func sorted(in []string) []string {
	out := append([]string(nil), in...)
	sort.Strings(out)
	return out
}

func wantContractOperations() map[string]contractOperation {
	return map[string]contractOperation{
		"acquireToken": {
			OperationID:           "acquireToken",
			Method:                "POST",
			Path:                  "/api/auth/token/acquire",
			RequestSchema:         "username-password",
			RequiredRequestFields: []string{"password", "username"},
			OptionalRequestFields: []string{"authSource"},
			SuccessStatus:         200,
			ResponseSchema:        "auth-token",
			QueryParameters:       nil,
			RequiresAuth:          false,
		},
		"testConnection": {
			OperationID:           "testConnection",
			Method:                "POST",
			Path:                  "/api/adapters/testConnection",
			RequestSchema:         "create-adapter-instance",
			RequiredRequestFields: []string{"adapterKindKey", "name"},
			OptionalRequestFields: wantCreateOptionalFields,
			SuccessStatus:         201,
			ResponseSchema:        "adapter-instance",
			QueryParameters:       nil,
			RequiresAuth:          true,
		},
		"createAdapterInstance": {
			OperationID:           "createAdapterInstance",
			Method:                "POST",
			Path:                  "/api/adapters",
			RequestSchema:         "create-adapter-instance",
			RequiredRequestFields: []string{"adapterKindKey", "name"},
			OptionalRequestFields: wantCreateOptionalFields,
			SuccessStatus:         201,
			ResponseSchema:        "adapter-instance",
			QueryParameters: []contractQueryParam{
				{Name: "extractIdentifierDefaults", Type: "boolean", Required: false, Default: false},
				{Name: "force", Type: "boolean", Required: false, Default: true},
			},
			RequiresAuth: true,
		},
	}
}

func TestContractSourceProvenance(t *testing.T) {
	var doc contractDoc
	readJSON(t, "docs/contract.json", &doc)

	for _, tc := range []struct {
		field string
		got   string
		want  string
	}{
		{"repository", doc.Source.Repository, wantRepository},
		{"specPath", doc.Source.SpecPath, wantSpecPath},
		{"commit", doc.Source.Commit, wantCommit},
		{"license", doc.Source.License, wantLicense},
		{"apiTitle", doc.Source.APITitle, wantAPITitle},
		{"apiVersion", doc.Source.APIVersion, wantAPIVersion},
		{"basePath", doc.Source.BasePath, wantBasePath},
	} {
		if tc.got != tc.want {
			t.Errorf("docs/contract.json source.%s = %q, want %q", tc.field, tc.got, tc.want)
		}
	}
}

func TestContractOperationsMatchSpecification(t *testing.T) {
	var doc contractDoc
	readJSON(t, "docs/contract.json", &doc)

	got := make(map[string]contractOperation, len(doc.Operations))
	var gotIDs []string
	for _, op := range doc.Operations {
		if _, dup := got[op.OperationID]; dup {
			t.Fatalf("docs/contract.json lists operationId %q twice", op.OperationID)
		}
		got[op.OperationID] = op
		gotIDs = append(gotIDs, op.OperationID)
	}
	if !reflect.DeepEqual(sorted(gotIDs), wantOperationIDs) {
		t.Fatalf("docs/contract.json operationIds = %v, want %v", sorted(gotIDs), wantOperationIDs)
	}

	for id, want := range wantContractOperations() {
		t.Run(id, func(t *testing.T) {
			have := got[id]
			if have.Method != want.Method {
				t.Errorf("method = %q, want %q", have.Method, want.Method)
			}
			if have.Path != want.Path {
				t.Errorf("path = %q, want %q", have.Path, want.Path)
			}
			if have.RequestSchema != want.RequestSchema {
				t.Errorf("requestSchema = %q, want %q", have.RequestSchema, want.RequestSchema)
			}
			if have.ResponseSchema != want.ResponseSchema {
				t.Errorf("responseSchema = %q, want %q", have.ResponseSchema, want.ResponseSchema)
			}
			if have.SuccessStatus != want.SuccessStatus {
				t.Errorf("successStatus = %d, want %d", have.SuccessStatus, want.SuccessStatus)
			}
			if have.RequiresAuth != want.RequiresAuth {
				t.Errorf("requiresAuth = %v, want %v", have.RequiresAuth, want.RequiresAuth)
			}
			if !reflect.DeepEqual(sorted(have.RequiredRequestFields), sorted(want.RequiredRequestFields)) {
				t.Errorf("requiredRequestFields = %v, want %v", sorted(have.RequiredRequestFields), sorted(want.RequiredRequestFields))
			}
			if !reflect.DeepEqual(sorted(have.OptionalRequestFields), sorted(want.OptionalRequestFields)) {
				t.Errorf("optionalRequestFields = %v, want %v", sorted(have.OptionalRequestFields), sorted(want.OptionalRequestFields))
			}

			gotParams := map[string]contractQueryParam{}
			for _, p := range have.QueryParameters {
				gotParams[p.Name] = p
			}
			if len(gotParams) != len(want.QueryParameters) {
				t.Fatalf("queryParameters = %v, want %d entries", have.QueryParameters, len(want.QueryParameters))
			}
			for _, wp := range want.QueryParameters {
				gp, ok := gotParams[wp.Name]
				if !ok {
					t.Errorf("queryParameters is missing %q", wp.Name)
					continue
				}
				if gp.Type != wp.Type {
					t.Errorf("queryParameters[%s].type = %q, want %q", wp.Name, gp.Type, wp.Type)
				}
				if gp.Required != wp.Required {
					t.Errorf("queryParameters[%s].required = %v, want %v", wp.Name, gp.Required, wp.Required)
				}
				if gp.Default != wp.Default {
					t.Errorf("queryParameters[%s].default = %v, want %v", wp.Name, gp.Default, wp.Default)
				}
			}
		})
	}
}

// TestContractPinsTheMock checks that the double really is pinned to the
// contract: it must serve exactly the operations the contract names, at the
// paths the contract derives, with the same authorization requirement.
func TestContractPinsTheMock(t *testing.T) {
	var doc contractDoc
	readJSON(t, "docs/contract.json", &doc)

	contractOps := map[string]contractOperation{}
	for _, op := range doc.Operations {
		contractOps[op.OperationID] = op
	}

	if len(opsmock.Operations) != len(contractOps) {
		t.Fatalf("mock serves %d operations, contract names %d", len(opsmock.Operations), len(contractOps))
	}
	for _, served := range opsmock.Operations {
		op, ok := contractOps[served.OperationID]
		if !ok {
			t.Errorf("mock serves %q which the contract does not name", served.OperationID)
			continue
		}
		if wantPath := doc.Source.BasePath + op.Path; served.Path != wantPath {
			t.Errorf("%s: mock path %q, contract derives %q", served.OperationID, served.Path, wantPath)
		}
		if served.Method != op.Method {
			t.Errorf("%s: mock method %q, contract says %q", served.OperationID, served.Method, op.Method)
		}
		if served.RequiresAuth != op.RequiresAuth {
			t.Errorf("%s: mock requiresAuth %v, contract says %v", served.OperationID, served.RequiresAuth, op.RequiresAuth)
		}
	}
}

func TestOfficialSourcesRecordsSpecRevision(t *testing.T) {
	var doc officialSourcesDoc
	readJSON(t, "docs/official_sources.json", &doc)

	if len(doc.Sources) != 1 {
		t.Fatalf("docs/official_sources.json lists %d sources, want exactly 1", len(doc.Sources))
	}

	var src *officialSource
	for i := range doc.Sources {
		if doc.Sources[i].Path == wantSpecPath {
			src = &doc.Sources[i]
			break
		}
	}
	if src == nil {
		t.Fatalf("docs/official_sources.json has no source with path %q", wantSpecPath)
	}

	if src.Repository != wantRepository {
		t.Errorf("repository = %q, want %q", src.Repository, wantRepository)
	}
	if src.Commit != wantCommit {
		t.Errorf("commit = %q, want %q", src.Commit, wantCommit)
	}
	if src.License != wantLicense {
		t.Errorf("license = %q, want %q", src.License, wantLicense)
	}
	if src.Title != wantAPITitle {
		t.Errorf("title = %q, want %q", src.Title, wantAPITitle)
	}
	if !reflect.DeepEqual(sorted(src.OperationIDs), wantOperationIDs) {
		t.Errorf("operationIds = %v, want %v", sorted(src.OperationIDs), wantOperationIDs)
	}
	wantURL := "https://github.com/" + wantRepository + "/blob/" + wantCommit + "/" + wantSpecPath
	if src.URL != wantURL {
		t.Errorf("url = %q, want pinned permalink %q", src.URL, wantURL)
	}
}
