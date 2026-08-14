package verifier_test

import (
	"context"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"sync"
	"testing"

	"vcfnetworks"
	"vcfnetworks/mockvcf"
)

const (
	wantCommit   = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
	wantSpecPath = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
)

func TestOfficialSourcesAndDerivedContract(t *testing.T) {
	t.Parallel()
	assertJSONObjectKeys(t, "../docs/contract.json", []string{"base_path", "openapi", "operations", "title", "version"})

	var sources struct {
		Repository   string   `json:"repository"`
		License      string   `json:"license"`
		Tag          string   `json:"tag"`
		Commit       string   `json:"commit"`
		SpecPath     string   `json:"spec_path"`
		OperationIDs []string `json:"operation_ids"`
	}
	readJSON(t, "../docs/official_sources.json", &sources)
	if sources.Repository != "https://github.com/vmware/vcf-api-specs" || sources.License != "Apache-2.0" || sources.Tag != "9.0.0.0" || sources.Commit != wantCommit || sources.SpecPath != wantSpecPath {
		t.Fatalf("official source pin does not identify the requested specification revision: %+v", sources)
	}
	if !reflect.DeepEqual(sources.OperationIDs, []string{"updateVcenter"}) {
		t.Fatalf("operation_ids = %v, want [updateVcenter]", sources.OperationIDs)
	}

	var contract struct {
		OpenAPI    string `json:"openapi"`
		Title      string `json:"title"`
		Version    string `json:"version"`
		BasePath   string `json:"base_path"`
		Operations []struct {
			OperationID    string `json:"operation_id"`
			Method         string `json:"method"`
			Path           string `json:"path"`
			PathParameters []struct {
				Name     string `json:"name"`
				In       string `json:"in"`
				Required bool   `json:"required"`
				Type     string `json:"type"`
			} `json:"path_parameters"`
			RequestBody struct {
				Required             bool   `json:"required"`
				ContentType          string `json:"content_type"`
				Schema               string `json:"schema"`
				UpdateableProperties struct {
					Nickname struct {
						Type     string `json:"type"`
						Required bool   `json:"required"`
					} `json:"nickname"`
					Notes struct {
						Type     string `json:"type"`
						Required bool   `json:"required"`
					} `json:"notes"`
					Credentials struct {
						Schema             string   `json:"schema"`
						Required           bool     `json:"required"`
						RequiredProperties []string `json:"required_properties"`
						OptionalProperties []string `json:"optional_properties"`
					} `json:"credentials"`
				} `json:"updateable_properties"`
			} `json:"request_body"`
			Security struct {
				Scheme      string `json:"scheme"`
				Type        string `json:"type"`
				In          string `json:"in"`
				Name        string `json:"name"`
				ValuePrefix string `json:"value_prefix"`
			} `json:"security"`
			Responses []struct {
				Status      int    `json:"status"`
				ContentType string `json:"content_type"`
				Schema      string `json:"schema"`
			} `json:"responses"`
		} `json:"operations"`
	}
	readJSON(t, "../docs/contract.json", &contract)
	if contract.OpenAPI != "3.0.1" || contract.Title != "VMware Cloud Foundation Operations for Networks API Reference" || contract.Version != "9.0.0.0" || contract.BasePath != "/api/ni" {
		t.Fatalf("unexpected contract identity: openapi=%q title=%q version=%q base_path=%q", contract.OpenAPI, contract.Title, contract.Version, contract.BasePath)
	}
	if len(contract.Operations) != 1 {
		t.Fatalf("contract has %d operations, want exactly 1", len(contract.Operations))
	}
	op := contract.Operations[0]
	if op.OperationID != "updateVcenter" || op.Method != http.MethodPut || op.Path != "/data-sources/vcenters/{id}" {
		t.Fatalf("unexpected operation wire contract: %+v", op)
	}
	if len(op.PathParameters) != 1 || op.PathParameters[0].Name != "id" || op.PathParameters[0].In != "path" || !op.PathParameters[0].Required || op.PathParameters[0].Type != "string" {
		t.Fatalf("unexpected path parameters: %+v", op.PathParameters)
	}
	if op.RequestBody.Required || op.RequestBody.ContentType != "application/json" || op.RequestBody.Schema != "VCenterDataSource" {
		t.Fatalf("unexpected request body contract: %+v", op.RequestBody)
	}
	props := op.RequestBody.UpdateableProperties
	if props.Nickname.Type != "string" || props.Nickname.Required || props.Notes.Type != "string" || props.Notes.Required {
		t.Fatalf("nickname/notes contract is not spec-derived: %+v", props)
	}
	if props.Credentials.Schema != "PasswordCredentials" || props.Credentials.Required || !reflect.DeepEqual(props.Credentials.RequiredProperties, []string{"username"}) || !reflect.DeepEqual(props.Credentials.OptionalProperties, []string{"password"}) {
		t.Fatalf("credentials contract is not spec-derived: %+v", props.Credentials)
	}
	if op.Security.Scheme != "ApiKeyAuth" || op.Security.Type != "apiKey" || op.Security.In != "header" || op.Security.Name != "Authorization" || op.Security.ValuePrefix != "NetworkInsight " {
		t.Fatalf("unexpected security contract: %+v", op.Security)
	}
	wantResponses := []struct {
		Status      int    `json:"status"`
		ContentType string `json:"content_type"`
		Schema      string `json:"schema"`
	}{
		{Status: 200, ContentType: "application/json", Schema: "VCenterDataSource"},
		{Status: 400, ContentType: "application/json", Schema: "ApiError"},
		{Status: 401},
		{Status: 403},
		{Status: 404},
		{Status: 500},
	}
	if !reflect.DeepEqual(op.Responses, wantResponses) {
		t.Fatalf("unexpected response contract:\n got: %+v\nwant: %+v", op.Responses, wantResponses)
	}
}

func TestRetryIsOneEffectAndWireShapeIsExact(t *testing.T) {
	tests := []struct {
		name         string
		failFirst    bool
		update       vcfnetworks.VCenterUpdate
		wantBody     string
		wantRequests int
	}{
		{
			name:         "normal response omits unset top-level optionals",
			update:       vcfnetworks.VCenterUpdate{Nickname: "Edge vCenter"},
			wantBody:     `{"nickname":"Edge vCenter"}`,
			wantRequests: 1,
		},
		{
			name:      "retry omits unset nested password",
			failFirst: true,
			update: vcfnetworks.VCenterUpdate{
				Nickname:    "Edge vCenter",
				Credentials: &vcfnetworks.PasswordCredentials{Username: "svc-vcf"},
			},
			wantBody:     `{"nickname":"Edge vCenter","credentials":{"username":"svc-vcf"}}`,
			wantRequests: 2,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()

			server := mockvcf.NewServer(tt.failFirst)
			defer server.Close()
			client := vcfnetworks.NewClient(server.URL(), "seed-token", http.DefaultClient)

			got, err := client.UpdateVcenter(context.Background(), "dc/edge 1", tt.update)
			if err != nil {
				t.Fatalf("UpdateVcenter() error = %v", err)
			}
			if got.EntityID != "dc/edge 1" || got.Nickname != "Edge vCenter" {
				t.Fatalf("UpdateVcenter() = %+v", got)
			}
			if effects := server.EffectCount(); effects != 1 {
				t.Fatalf("EffectCount() = %d, want 1", effects)
			}
			requests := server.Requests()
			if len(requests) != tt.wantRequests {
				t.Fatalf("request count = %d, want %d", len(requests), tt.wantRequests)
			}
			var wantJSON any
			if err := json.Unmarshal([]byte(tt.wantBody), &wantJSON); err != nil {
				t.Fatalf("invalid expected JSON: %v", err)
			}
			for i, request := range requests {
				if request.Method != http.MethodPut {
					t.Errorf("request %d method = %q, want PUT", i, request.Method)
				}
				parsedURI, err := url.ParseRequestURI(request.RequestURI)
				if err != nil {
					t.Errorf("request %d URI %q is invalid: %v", i, request.RequestURI, err)
				} else {
					const prefix = "/api/ni/data-sources/vcenters/"
					escapedPath := parsedURI.EscapedPath()
					escapedID := strings.TrimPrefix(escapedPath, prefix)
					decodedID, decodeErr := url.PathUnescape(escapedID)
					if parsedURI.RawQuery != "" || !strings.HasPrefix(escapedPath, prefix) || strings.Contains(escapedID, "/") || decodeErr != nil || decodedID != "dc/edge 1" {
						t.Errorf("request %d URI = %q, want the contract path with id encoded as one segment", i, request.RequestURI)
					}
				}
				if request.Header.Get("Authorization") != "NetworkInsight seed-token" {
					t.Errorf("request %d Authorization = %q", i, request.Header.Get("Authorization"))
				}
				if request.Header.Get("Content-Type") != "application/json" {
					t.Errorf("request %d Content-Type = %q", i, request.Header.Get("Content-Type"))
				}
				var gotJSON any
				if err := json.Unmarshal(request.Body, &gotJSON); err != nil {
					t.Errorf("request %d body is not JSON: %v", i, err)
				} else if !reflect.DeepEqual(gotJSON, wantJSON) {
					t.Errorf("request %d body = %s, want JSON equivalent to %s", i, request.Body, tt.wantBody)
				}
			}
			if tt.failFirst && string(requests[0].Body) != string(requests[1].Body) {
				t.Fatalf("retry body changed: first=%q second=%q", requests[0].Body, requests[1].Body)
			}
			originalBody := string(requests[0].Body)
			requests[0].Body[0] = 'x'
			requests[0].Header.Set("Authorization", "changed")
			fresh := server.Requests()
			if string(fresh[0].Body) != originalBody || fresh[0].Header.Get("Authorization") != "NetworkInsight seed-token" {
				t.Fatal("Requests() did not return defensive snapshots")
			}
		})
	}
}

func TestMockRejectsOperationsOutsideContract(t *testing.T) {
	t.Parallel()

	server := mockvcf.NewServer(false)
	defer server.Close()

	tests := []struct {
		method string
		path   string
	}{
		{method: http.MethodGet, path: "/api/ni/info/version"},
		{method: http.MethodPost, path: "/api/ni/data-sources/vcenters/id"},
		{method: http.MethodPut, path: "/api/ni/data-sources/vcenters/id/extra"},
		{method: http.MethodPut, path: "/api/ni/data-sources/vcenters/id?force=true"},
	}
	for _, tt := range tests {
		req, err := http.NewRequest(tt.method, server.URL()+tt.path, nil)
		if err != nil {
			t.Fatal(err)
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		_, _ = io.Copy(io.Discard, resp.Body)
		_ = resp.Body.Close()
		if resp.StatusCode != http.StatusNotFound {
			t.Errorf("%s %s status = %d, want 404", tt.method, tt.path, resp.StatusCode)
		}
	}
}

func TestUpdateVcenterReturnsErrorsWithoutUndeclaredRetries(t *testing.T) {
	tests := []struct {
		name         string
		status       int
		responseBody string
		wantRequests int
	}{
		{
			name:         "declared non-retryable error",
			status:       http.StatusBadRequest,
			responseBody: `{"code":400,"message":"invalid update"}`,
			wantRequests: 1,
		},
		{
			name:         "second declared 500",
			status:       http.StatusInternalServerError,
			wantRequests: 2,
		},
		{
			name:         "invalid declared success body",
			status:       http.StatusOK,
			responseBody: `{"entity_id":`,
			wantRequests: 1,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()

			var (
				mu     sync.Mutex
				bodies [][]byte
			)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				body, err := io.ReadAll(r.Body)
				if err != nil {
					http.Error(w, "read body", http.StatusBadRequest)
					return
				}
				mu.Lock()
				bodies = append(bodies, append([]byte(nil), body...))
				mu.Unlock()
				if tt.responseBody != "" {
					w.Header().Set("Content-Type", "application/json")
				}
				w.WriteHeader(tt.status)
				_, _ = io.WriteString(w, tt.responseBody)
			}))
			defer server.Close()

			client := vcfnetworks.NewClient(server.URL, "error-token", server.Client())
			_, err := client.UpdateVcenter(context.Background(), "error-id", vcfnetworks.VCenterUpdate{Notes: "unchanged"})
			if err == nil {
				t.Fatal("UpdateVcenter() returned nil error")
			}

			mu.Lock()
			gotBodies := make([][]byte, len(bodies))
			for i := range bodies {
				gotBodies[i] = append([]byte(nil), bodies[i]...)
			}
			mu.Unlock()
			if len(gotBodies) != tt.wantRequests {
				t.Fatalf("request count = %d, want %d", len(gotBodies), tt.wantRequests)
			}
			if len(gotBodies) == 2 && !reflect.DeepEqual(gotBodies[0], gotBodies[1]) {
				t.Fatalf("retry body changed: first=%q second=%q", gotBodies[0], gotBodies[1])
			}
		})
	}
}

func TestMockConcurrentSnapshotsAreRaceSafe(t *testing.T) {
	t.Parallel()

	server := mockvcf.NewServer(false)
	defer server.Close()
	client := vcfnetworks.NewClient(server.URL(), "race-token", http.DefaultClient)

	stopSnapshots := make(chan struct{})
	snapshotsStopped := make(chan struct{})
	go func() {
		defer close(snapshotsStopped)
		for {
			select {
			case <-stopSnapshots:
				return
			default:
				_ = server.Requests()
				_ = server.EffectCount()
			}
		}
	}()

	const requestCount = 12
	errors := make(chan error, requestCount)
	var workers sync.WaitGroup
	for i := 0; i < requestCount; i++ {
		i := i
		workers.Add(1)
		go func() {
			defer workers.Done()
			_, err := client.UpdateVcenter(context.Background(), "race-id", vcfnetworks.VCenterUpdate{Nickname: "same-center"})
			if err != nil {
				errors <- fmt.Errorf("request %d: %w", i, err)
			}
		}()
	}
	workers.Wait()
	close(stopSnapshots)
	<-snapshotsStopped
	close(errors)
	for err := range errors {
		t.Error(err)
	}
	if t.Failed() {
		return
	}
	if got := len(server.Requests()); got != requestCount {
		t.Fatalf("request count = %d, want %d", got, requestCount)
	}
	if got := server.EffectCount(); got != 1 {
		t.Fatalf("EffectCount() = %d, want 1 for identical concurrent representations", got)
	}
}

func TestVcfnetworksPackageIncludesTableDrivenTests(t *testing.T) {
	t.Parallel()

	testFiles, err := filepath.Glob(filepath.Join("..", "*_test.go"))
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range testFiles {
		parsed, err := parser.ParseFile(token.NewFileSet(), path, nil, 0)
		if err != nil {
			t.Fatalf("parse %s: %v", path, err)
		}
		for _, declaration := range parsed.Decls {
			function, ok := declaration.(*ast.FuncDecl)
			if !ok || function.Recv != nil || function.Body == nil || len(function.Name.Name) <= len("Test") || function.Name.Name[:len("Test")] != "Test" {
				continue
			}
			hasTableIteration := false
			ast.Inspect(function.Body, func(node ast.Node) bool {
				switch node.(type) {
				case *ast.RangeStmt, *ast.ForStmt:
					hasTableIteration = true
				}
				return true
			})
			if hasTableIteration {
				return
			}
		}
	}
	t.Fatal("vcfnetworks package has no table-driven Test function in a root *_test.go file")
}

func readJSON(t *testing.T, path string, dst any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, dst); err != nil {
		t.Fatalf("parse %s: %v", path, err)
	}
}

func assertJSONObjectKeys(t *testing.T, path string, want []string) {
	t.Helper()
	var object map[string]json.RawMessage
	readJSON(t, path, &object)
	got := make([]string, 0, len(object))
	for key := range object {
		got = append(got, key)
	}
	slices.Sort(got)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("%s top-level keys = %v, want %v", path, got, want)
	}
}
