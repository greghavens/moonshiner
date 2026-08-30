package verifier_test

import (
	"context"
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"testing"

	"example.com/vcf-networks-client/internal/contractmock"
	"example.com/vcf-networks-client/networks"
)

func repoFile(t *testing.T, elements ...string) string {
	t.Helper()
	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve verifier source path")
	}
	parts := append([]string{filepath.Dir(currentFile), ".."}, elements...)
	return filepath.Join(parts...)
}

func TestListAllTroubleshootingIncidentsWireAndResult(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name             string
		options          networks.ListTroubleshootingIncidentsOptions
		expectedRequests []string
	}{
		{
			name: "explicit optional parameters",
			options: networks.ListTroubleshootingIncidentsOptions{
				Size:          2,
				StartEntityID: "VirtualMachine:vm 1",
			},
			expectedRequests: []string{
				"/api/ni/gnt/troubleshoot/incidents?size=2&start_entity_id=VirtualMachine%3Avm+1",
				"/api/ni/gnt/troubleshoot/incidents?cursor=page%2B2%2F%3D%3D&size=2&start_entity_id=VirtualMachine%3Avm+1",
				"/api/ni/gnt/troubleshoot/incidents?cursor=page%2B3%2F%3D%3D&size=2&start_entity_id=VirtualMachine%3Avm+1",
			},
		},
		{
			name:    "caller optional fields unset",
			options: networks.ListTroubleshootingIncidentsOptions{},
			expectedRequests: []string{
				"/api/ni/gnt/troubleshoot/incidents",
				"/api/ni/gnt/troubleshoot/incidents?cursor=page%2B2%2F%3D%3D",
				"/api/ni/gnt/troubleshoot/incidents?cursor=page%2B3%2F%3D%3D",
			},
		},
	}

	wantIncidents := []networks.TroubleshootingIncident{
		{EntityID: "entity-010", StartEntityID: "vm-1", Name: "First", Status: "RUNNING"},
		{EntityID: "entity-015", StartEntityID: "vm-15", Name: "Between", Status: "COMPLETED"},
		{EntityID: "entity-020", StartEntityID: "vm-2", Name: "Second", Status: "FAILED"},
		{EntityID: "entity-030", StartEntityID: "vm-3", Name: "Third", Status: "COMPLETED"},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			server, err := contractmock.NewWithPages(repoFile(t, "docs", "contract.json"), 3)
			if err != nil {
				t.Fatalf("start contract mock: %v", err)
			}
			defer server.Close()

			client, err := networks.NewClient(server.URL(), "fixture-token", server.Client())
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			got, err := client.ListAllTroubleshootingIncidents(context.Background(), test.options)
			if err != nil {
				t.Fatalf("ListAllTroubleshootingIncidents: %v", err)
			}
			if !reflect.DeepEqual(got, wantIncidents) {
				t.Fatalf("incidents mismatch\n got: %#v\nwant: %#v", got, wantIncidents)
			}

			requests := server.Requests()
			if len(requests) != len(test.expectedRequests) {
				t.Fatalf("got %d requests, want %d: %#v", len(requests), len(test.expectedRequests), requests)
			}
			for i, wantURI := range test.expectedRequests {
				request := requests[i]
				if request.Method != http.MethodGet {
					t.Errorf("request %d method = %q, want GET", i, request.Method)
				}
				if request.Authorization != "NetworkInsight fixture-token" {
					t.Errorf("request %d Authorization = %q", i, request.Authorization)
				}
				if len(request.Body) != 0 {
					t.Errorf("request %d body = %q, want no body", i, request.Body)
				}

				parsed, err := url.ParseRequestURI(request.RequestURI)
				if err != nil {
					t.Fatalf("parse request %d URI: %v", i, err)
				}
				wantParsed, err := url.ParseRequestURI(wantURI)
				if err != nil {
					t.Fatalf("parse expected request %d URI: %v", i, err)
				}
				if parsed.Path != wantParsed.Path {
					t.Errorf("request %d path = %q, want %q", i, parsed.Path, wantParsed.Path)
				}
				query := parsed.Query()
				if wantQuery := wantParsed.Query(); !reflect.DeepEqual(query, wantQuery) {
					t.Errorf("request %d query = %#v, want %#v", i, query, wantQuery)
				}
				if test.options.StartEntityID == "" {
					if _, present := query["start_entity_id"]; present {
						t.Errorf("request %d sent unset start_entity_id: %q", i, query.Get("start_entity_id"))
					}
				} else if got := query.Get("start_entity_id"); got != test.options.StartEntityID {
					t.Errorf("request %d start_entity_id = %q, want %q", i, got, test.options.StartEntityID)
				}
				if i == 0 {
					if _, present := query["cursor"]; present {
						t.Errorf("first request sent cursor: %q", query.Get("cursor"))
					}
				}
				if test.options.Size == 0 {
					if _, present := query["size"]; present {
						t.Errorf("request %d sent unset size: %q", i, query.Get("size"))
					}
				}
			}
		})
	}
}

func TestListAllTroubleshootingIncidentsErrors(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name     string
		response contractmock.ResponseOverride
	}{
		{
			name: "non-2xx response",
			response: contractmock.ResponseOverride{
				StatusCode: http.StatusServiceUnavailable,
				Body:       `{"message":"fixture unavailable"}`,
			},
		},
		{
			name: "malformed JSON response",
			response: contractmock.ResponseOverride{
				StatusCode: http.StatusOK,
				Body:       `{"results":[`,
			},
		},
		{
			name: "trailing JSON response",
			response: contractmock.ResponseOverride{
				StatusCode: http.StatusOK,
				Body:       `{"results":[],"total_count":0} false`,
			},
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			server, err := contractmock.NewWithResponse(
				repoFile(t, "docs", "contract.json"),
				&test.response,
			)
			if err != nil {
				t.Fatalf("start contract mock: %v", err)
			}
			defer server.Close()

			client, err := networks.NewClient(server.URL(), "fixture-token", server.Client())
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			if _, err := client.ListAllTroubleshootingIncidents(
				context.Background(),
				networks.ListTroubleshootingIncidentsOptions{},
			); err == nil {
				t.Fatal("ListAllTroubleshootingIncidents returned nil error")
			}
			if got := len(server.Requests()); got != 1 {
				t.Fatalf("mock received %d requests, want 1", got)
			}
		})
	}
}

func TestNetworksPackageIncludesTableDrivenTests(t *testing.T) {
	t.Parallel()

	directory := repoFile(t, "networks")
	entries, err := os.ReadDir(directory)
	if err != nil {
		t.Fatalf("read networks package: %v", err)
	}
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".go" || len(entry.Name()) < len("_test.go") || entry.Name()[len(entry.Name())-len("_test.go"):] != "_test.go" {
			continue
		}
		parsed, err := parser.ParseFile(token.NewFileSet(), filepath.Join(directory, entry.Name()), nil, 0)
		if err != nil {
			t.Fatalf("parse %s: %v", entry.Name(), err)
		}
		for _, declaration := range parsed.Decls {
			function, ok := declaration.(*ast.FuncDecl)
			if !ok || function.Recv != nil || len(function.Name.Name) <= len("Test") || function.Name.Name[:len("Test")] != "Test" {
				continue
			}
			hasIteration := false
			ast.Inspect(function.Body, func(node ast.Node) bool {
				switch node.(type) {
				case *ast.RangeStmt, *ast.ForStmt:
					hasIteration = true
				}
				return true
			})
			if hasIteration {
				return
			}
		}
	}
	t.Fatal("networks package has no table-driven Test function in a *_test.go file")
}

func TestOfficialContractProvenance(t *testing.T) {
	t.Parallel()

	type source struct {
		SpecPath  string `json:"specPath"`
		Tag       string `json:"tag"`
		CommitSHA string `json:"commitSha"`
		Operation string `json:"operationId"`
		Method    string `json:"method"`
		Path      string `json:"path"`
	}
	type provenance struct {
		Repository string   `json:"repository"`
		License    string   `json:"license"`
		Sources    []source `json:"sources"`
	}

	raw, err := os.ReadFile(repoFile(t, "docs", "official_sources.json"))
	if err != nil {
		t.Fatalf("read official sources: %v", err)
	}
	var got provenance
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatalf("decode official sources: %v", err)
	}
	want := provenance{
		Repository: "https://github.com/vmware/vcf-api-specs",
		License:    "Apache-2.0",
		Sources: []source{{
			SpecPath:  "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml",
			Tag:       "9.0.0.0",
			CommitSHA: "85151f6b1bb58f13b6ac0304bfec53904bea085f",
			Operation: "listTroubleshootingIncidents",
			Method:    "GET",
			Path:      "/gnt/troubleshoot/incidents",
		}},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("official source provenance mismatch\n got: %#v\nwant: %#v", got, want)
	}
}
