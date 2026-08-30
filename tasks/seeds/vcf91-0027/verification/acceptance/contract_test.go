package acceptance_test

import (
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"example.com/vcf91hosts/internal/contractmock"
)

const (
	commitSHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	specPath  = "specifications/sddc-manager/sddc-manager-openapi.json"
	repoURL   = "https://github.com/vmware/vcf-api-specs"
)

type parameter struct {
	Name       string `json:"name"`
	In         string `json:"in"`
	Type       string `json:"type"`
	Required   bool   `json:"required"`
	Deprecated bool   `json:"deprecated"`
}

type response struct {
	Status    int    `json:"status"`
	MediaType string `json:"mediaType"`
	Schema    string `json:"schema"`
}

type operation struct {
	OperationID     string      `json:"operationId"`
	Method          string      `json:"method"`
	Path            string      `json:"path"`
	QueryParameters []parameter `json:"queryParameters"`
	Responses       []response  `json:"responses"`
}

type property struct {
	Name     string `json:"name"`
	Type     string `json:"type"`
	Format   string `json:"format,omitempty"`
	Items    string `json:"items,omitempty"`
	ReadOnly bool   `json:"readOnly"`
}

type schema struct {
	Type       string     `json:"type"`
	Properties []property `json:"properties"`
}

type contract struct {
	Specification struct {
		Repository     string `json:"repository"`
		CommitSHA      string `json:"commitSha"`
		Path           string `json:"path"`
		OpenAPIVersion string `json:"openapiVersion"`
		APIVersion     string `json:"apiVersion"`
	} `json:"specification"`
	Operations []operation       `json:"operations"`
	Schemas    map[string]schema `json:"schemas"`
}

func TestSpecDerivedContract(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "docs", "contract.json"))
	if err != nil {
		t.Fatal(err)
	}
	var got contract
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatalf("decode contract.json: %v", err)
	}

	if got.Specification.Repository != repoURL ||
		got.Specification.CommitSHA != commitSHA ||
		got.Specification.Path != specPath ||
		got.Specification.OpenAPIVersion != "3.0.1" ||
		got.Specification.APIVersion != "9.1.0.0" {
		t.Fatalf("wrong pinned specification coordinates: %#v", got.Specification)
	}

	wantParameters := []parameter{
		{Name: "pageSize", In: "query", Type: "integer"},
		{Name: "pageNumber", In: "query", Type: "integer"},
		{Name: "fqdn", In: "query", Type: "string"},
		{Name: "status", In: "query", Type: "string"},
		{Name: "domainId", In: "query", Type: "string"},
		{Name: "clusterId", In: "query", Type: "string"},
		{Name: "networkpoolId", In: "query", Type: "string"},
		{Name: "storageType", In: "query", Type: "string"},
		{Name: "datastoreName", In: "query", Type: "string"},
		{Name: "ipAddressVersionForVmotion", In: "query", Type: "string"},
		{Name: "isStandalone", In: "query", Type: "boolean"},
		{Name: "isLifecycleManaged", In: "query", Type: "boolean"},
		{Name: "isVsanWitnessHost", In: "query", Type: "boolean"},
		{Name: "size", In: "query", Type: "integer", Deprecated: true},
		{Name: "page", In: "query", Type: "integer", Deprecated: true},
	}
	wantOperations := []operation{{
		OperationID:     "getHosts",
		Method:          "GET",
		Path:            "/v1/hosts",
		QueryParameters: wantParameters,
		Responses: []response{{
			Status:    200,
			MediaType: "application/json",
			Schema:    "PageOfHost",
		}},
	}}
	if !reflect.DeepEqual(got.Operations, wantOperations) {
		t.Fatalf("operation projection differs from pinned OpenAPI\n got: %#v\nwant: %#v", got.Operations, wantOperations)
	}

	wantSchemas := map[string]schema{
		"PageOfHost": {
			Type: "object",
			Properties: []property{
				{Name: "elements", Type: "array", Items: "Host"},
				{Name: "pageMetadata", Type: "PageMetadata"},
			},
		},
		"PageMetadata": {
			Type: "object",
			Properties: []property{
				{Name: "pageNumber", Type: "integer", Format: "int32", ReadOnly: true},
				{Name: "pageSize", Type: "integer", Format: "int32", ReadOnly: true},
				{Name: "totalElements", Type: "integer", Format: "int32", ReadOnly: true},
				{Name: "totalPages", Type: "integer", Format: "int32", ReadOnly: true},
			},
		},
		"Host": {
			Type: "object",
			Properties: []property{
				{Name: "id", Type: "string", ReadOnly: true},
				{Name: "fqdn", Type: "string", ReadOnly: true},
				{Name: "status", Type: "string", ReadOnly: true},
			},
		},
	}
	if !reflect.DeepEqual(got.Schemas, wantSchemas) {
		t.Fatalf("schema projection differs from pinned OpenAPI\n got: %#v\nwant: %#v", got.Schemas, wantSchemas)
	}
}

func TestOfficialSources(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "docs", "official_sources.json"))
	if err != nil {
		t.Fatal(err)
	}
	var got struct {
		Repository   string   `json:"repository"`
		CommitSHA    string   `json:"commitSha"`
		SpecPath     string   `json:"specificationPath"`
		License      string   `json:"license"`
		OperationIDs []string `json:"operationIds"`
	}
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatalf("decode official_sources.json: %v", err)
	}

	if got.Repository != repoURL ||
		got.CommitSHA != commitSHA ||
		got.SpecPath != specPath ||
		got.License != "Apache-2.0" ||
		!reflect.DeepEqual(got.OperationIDs, []string{"getHosts"}) {
		t.Fatalf("official source provenance is not exact: %#v", got)
	}
}

func TestLoopbackMockServesOnlyContractOperations(t *testing.T) {
	t.Parallel()

	mock := contractmock.New(t, filepath.Join("..", "docs", "contract.json"), [][]contractmock.Host{{
		{ID: "host-1", FQDN: "alpha.example.test", Status: "ASSIGNED"},
	}})
	response, err := mock.Client().Get(mock.URL() + "/v1/domains?pageNumber=0")
	if err != nil {
		t.Fatalf("request unsupported operation: %v", err)
	}
	defer response.Body.Close()
	if _, err := io.Copy(io.Discard, response.Body); err != nil {
		t.Fatalf("consume unsupported operation response: %v", err)
	}
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("unsupported operation status = %d, want 404", response.StatusCode)
	}

	requests := mock.Requests()
	if len(requests) != 1 || requests[0].Path != "/v1/domains" {
		t.Fatalf("request log did not capture unsupported operation exactly: %#v", requests)
	}
}
