package verifier_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"sync"
	"testing"

	"vcfnetworks/mockvcf"
	"vcfnetworks/networks"
)

const (
	specPath = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
	commit   = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
)

type officialSources struct {
	Repository   string `json:"repository"`
	Tag          string `json:"tag"`
	CommitSHA    string `json:"commit_sha"`
	SpecPath     string `json:"spec_path"`
	License      string `json:"license"`
	OperationIDs []struct {
		OperationID string `json:"operation_id"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	} `json:"operation_ids"`
}

type contract struct {
	Source struct {
		Repository string `json:"repository"`
		Tag        string `json:"tag"`
		CommitSHA  string `json:"commit_sha"`
		SpecPath   string `json:"spec_path"`
		License    string `json:"license"`
	} `json:"source"`
	ServerBasePath string `json:"server_base_path"`
	Authentication struct {
		Type   string `json:"type"`
		In     string `json:"in"`
		Name   string `json:"name"`
		Format string `json:"format"`
	} `json:"authentication"`
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
		Request *struct {
			Required    bool   `json:"required"`
			ContentType string `json:"content_type"`
			Schema      string `json:"schema"`
		} `json:"request,omitempty"`
		SuccessResponse struct {
			Status      int    `json:"status"`
			ContentType string `json:"content_type"`
			Schema      string `json:"schema"`
		} `json:"success_response"`
	} `json:"operations"`
	Schemas map[string]struct {
		Type       string   `json:"type"`
		Required   []string `json:"required"`
		Properties map[string]struct {
			Type    string   `json:"type"`
			Default *string  `json:"default,omitempty"`
			Enum    []string `json:"enum,omitempty"`
			Items   string   `json:"items,omitempty"`
			Format  string   `json:"format,omitempty"`
		} `json:"properties"`
	} `json:"schemas"`
}

func readJSON(t *testing.T, name string, target any) {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("..", "docs", name))
	if err != nil {
		t.Fatalf("read %s: %v", name, err)
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		t.Fatalf("decode %s: %v", name, err)
	}
	if err := decoder.Decode(&struct{}{}); err != io.EOF {
		t.Fatalf("%s has trailing JSON", name)
	}
}

func TestOfficialSourcesArePinnedToTheSpecification(t *testing.T) {
	var got officialSources
	readJSON(t, "official_sources.json", &got)

	if got.Repository != "https://github.com/vmware/vcf-api-specs" || got.Tag != "9.0.0.0" || got.CommitSHA != commit || got.SpecPath != specPath || got.License != "Apache-2.0" {
		t.Fatalf("unexpected source pin: %+v", got)
	}
	want := []struct{ id, method, path string }{
		{"updateCertificate", http.MethodPut, "/settings/certificates/{id}"},
		{"fetchCertificateUpdateStatusForUpdateId", http.MethodGet, "/settings/certificates/status/{id}"},
	}
	if len(got.OperationIDs) != len(want) {
		t.Fatalf("operation source count = %d, want %d", len(got.OperationIDs), len(want))
	}
	for index, expected := range want {
		actual := got.OperationIDs[index]
		if actual.OperationID != expected.id || actual.Method != expected.method || actual.Path != expected.path {
			t.Errorf("operation source %d = %+v, want %+v", index, actual, expected)
		}
	}
}

func TestContractMatchesPinnedOpenAPIExtraction(t *testing.T) {
	var got contract
	readJSON(t, "contract.json", &got)
	if got.Source.CommitSHA != commit || got.Source.SpecPath != specPath || got.Source.Tag != "9.0.0.0" || got.Source.Repository != "https://github.com/vmware/vcf-api-specs" || got.Source.License != "Apache-2.0" {
		t.Fatalf("contract source pin is incorrect: %+v", got.Source)
	}
	if got.ServerBasePath != "/api/ni" {
		t.Errorf("server base path = %q", got.ServerBasePath)
	}
	if got.Authentication.Type != "apiKey" || got.Authentication.In != "header" || got.Authentication.Name != "Authorization" || got.Authentication.Format != "NetworkInsight {token}" {
		t.Errorf("authentication = %+v", got.Authentication)
	}

	tests := []struct {
		id, method, path string
		status           int
		requestSchema    string
	}{
		{"updateCertificate", http.MethodPut, "/settings/certificates/{id}", http.StatusAccepted, "CertificateUpdateRequest"},
		{"fetchCertificateUpdateStatusForUpdateId", http.MethodGet, "/settings/certificates/status/{id}", http.StatusOK, ""},
	}
	if len(got.Operations) != len(tests) {
		t.Fatalf("contract operation count = %d, want %d", len(got.Operations), len(tests))
	}
	for index, test := range tests {
		op := got.Operations[index]
		if op.OperationID != test.id || op.Method != test.method || op.Path != test.path || op.SuccessResponse.Status != test.status || op.SuccessResponse.ContentType != "application/json" || op.SuccessResponse.Schema != "CertificateUpdateStatus" {
			t.Errorf("operation %d does not match specification: %+v", index, op)
		}
		if test.requestSchema == "" && op.Request != nil {
			t.Errorf("%s unexpectedly has a request body", test.id)
		}
		if test.requestSchema != "" && (op.Request == nil || !op.Request.Required || op.Request.ContentType != "application/json" || op.Request.Schema != test.requestSchema) {
			t.Errorf("%s request = %+v", test.id, op.Request)
		}
		wantParameter := struct {
			Name     string `json:"name"`
			In       string `json:"in"`
			Required bool   `json:"required"`
			Type     string `json:"type"`
		}{Name: "id", In: "path", Required: true, Type: "string"}
		if len(op.PathParameters) != 1 || op.PathParameters[0] != wantParameter {
			t.Errorf("%s path parameters = %+v", test.id, op.PathParameters)
		}
	}

	if !reflect.DeepEqual(sortedMapKeys(got.Schemas), []string{"CertificateUpdateRequest", "CertificateUpdateStatus", "Node"}) {
		t.Fatalf("schema names = %v", sortedMapKeys(got.Schemas))
	}
	requestSchema := got.Schemas["CertificateUpdateRequest"]
	if requestSchema.Type != "object" || requestSchema.Required == nil || len(requestSchema.Required) != 0 {
		t.Errorf("certificate request required fields = %v", requestSchema.Required)
	}
	if !reflect.DeepEqual(sortedMapKeys(requestSchema.Properties), []string{"certificate", "chain", "private_key"}) {
		t.Errorf("certificate request properties = %v", sortedMapKeys(requestSchema.Properties))
	}
	for _, field := range []string{"certificate", "private_key", "chain"} {
		if requestSchema.Properties[field].Type != "string" {
			t.Errorf("request property %q missing or not string", field)
		}
	}
	chain := requestSchema.Properties["chain"]
	if chain.Default == nil || *chain.Default != "" {
		t.Errorf("chain default = %v, want empty string", chain.Default)
	}
	status := got.Schemas["CertificateUpdateStatus"].Properties["status"]
	if !reflect.DeepEqual(status.Enum, []string{"SUBMITTED", "IN_PROGRESS", "SUCCESS", "FAILED"}) {
		t.Errorf("status enum = %v", status.Enum)
	}
	statusSchema := got.Schemas["CertificateUpdateStatus"]
	if statusSchema.Type != "object" || statusSchema.Required == nil || len(statusSchema.Required) != 0 {
		t.Errorf("certificate status required fields = %v", statusSchema.Required)
	}
	if !reflect.DeepEqual(sortedMapKeys(statusSchema.Properties), []string{
		"error_message", "failed_nodes", "id", "last_modified_by_user", "last_modified_time", "name", "status", "updated_nodes",
	}) {
		t.Errorf("certificate status properties = %v", sortedMapKeys(statusSchema.Properties))
	}
	for _, field := range []string{"id", "name", "status", "error_message", "last_modified_by_user"} {
		if statusSchema.Properties[field].Type != "string" {
			t.Errorf("status property %q = %+v", field, statusSchema.Properties[field])
		}
	}
	for _, field := range []string{"failed_nodes", "updated_nodes"} {
		property := statusSchema.Properties[field]
		if property.Type != "array" || property.Items != "Node" {
			t.Errorf("status property %q = %+v", field, property)
		}
	}
	modified := statusSchema.Properties["last_modified_time"]
	if modified.Type != "integer" || modified.Format != "int64" {
		t.Errorf("last_modified_time = %+v", modified)
	}
	nodeSchema := got.Schemas["Node"]
	if nodeSchema.Type != "object" || nodeSchema.Required == nil || len(nodeSchema.Required) != 0 ||
		!reflect.DeepEqual(sortedMapKeys(nodeSchema.Properties), []string{"id", "name"}) ||
		nodeSchema.Properties["id"].Type != "string" || nodeSchema.Properties["name"].Type != "string" {
		t.Errorf("Node schema = %+v", nodeSchema)
	}
}

func sortedMapKeys[T any](values map[string]T) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	slices.Sort(keys)
	return keys
}

func TestUpdateCertificateWireAndPolling(t *testing.T) {
	tests := []struct {
		name          string
		certificate   string
		privateKey    string
		certificateID string
		updateID      string
		token         string
		submitURI     string
		pollURI       string
		initialState  networks.UpdateStatus
		polls         []networks.CertificateUpdateStatus
		wantTerminal  networks.CertificateUpdateStatus
		wantError     string
	}{
		{
			name:          "polls after accepted success-looking response through in progress to success",
			certificate:   "-----BEGIN CERTIFICATE-----\nsuccess fixture\n-----END CERTIFICATE-----",
			privateKey:    "-----BEGIN PRIVATE KEY-----\nsuccess fixture\n-----END PRIVATE KEY-----",
			certificateID: "proxy register.crt",
			updateID:      "update-42",
			token:         "success-token",
			submitURI:     "/api/ni/settings/certificates/proxy%20register.crt",
			pollURI:       "/api/ni/settings/certificates/status/update-42",
			initialState:  networks.StatusSuccess,
			polls: []networks.CertificateUpdateStatus{
				{ID: "update-42", Status: networks.StatusInProgress},
				{ID: "terminal-success", Name: "proxy_register.crt", Status: networks.StatusSuccess, UpdatedNodes: []networks.Node{{ID: "node-1", Name: "platform-1"}}, LastModifiedBy: "admin@example.test", LastModifiedAt: 1700000000001},
			},
			wantTerminal: networks.CertificateUpdateStatus{ID: "terminal-success", Name: "proxy_register.crt", Status: networks.StatusSuccess, UpdatedNodes: []networks.Node{{ID: "node-1", Name: "platform-1"}}, LastModifiedBy: "admin@example.test", LastModifiedAt: 1700000000001},
		},
		{
			name:          "polls after accepted failed-looking response through submitted to failed",
			certificate:   "-----BEGIN CERTIFICATE-----\nfailure fixture\n-----END CERTIFICATE-----",
			privateKey:    "-----BEGIN PRIVATE KEY-----\nfailure fixture\n-----END PRIVATE KEY-----",
			certificateID: "client.crt",
			updateID:      "update-99",
			token:         "failure-token",
			submitURI:     "/api/ni/settings/certificates/client.crt",
			pollURI:       "/api/ni/settings/certificates/status/update-99",
			initialState:  networks.StatusFailed,
			polls: []networks.CertificateUpdateStatus{
				{ID: "update-99", Status: networks.StatusSubmitted},
				{ID: "terminal-failure", Name: "proxy_register.crt", Status: networks.StatusFailed, ErrorMessage: "certificate rejected by node", FailedNodes: []networks.Node{{ID: "node-2", Name: "collector-2"}}, LastModifiedBy: "admin@example.test", LastModifiedAt: 1700000000002},
			},
			wantTerminal: networks.CertificateUpdateStatus{ID: "terminal-failure", Name: "proxy_register.crt", Status: networks.StatusFailed, ErrorMessage: "certificate rejected by node", FailedNodes: []networks.Node{{ID: "node-2", Name: "collector-2"}}, LastModifiedBy: "admin@example.test", LastModifiedAt: 1700000000002},
			wantError:    "certificate rejected by node",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := mockvcf.New(mockvcf.Script{
				Initial: networks.CertificateUpdateStatus{ID: test.updateID, Name: "accepted-response", Status: test.initialState},
				Polls:   test.polls,
			})
			defer server.Close()

			client := networks.NewClient(server.URL(), test.token, server.Client(), 0)
			status, err := client.UpdateCertificateAndWait(context.Background(), test.certificateID, networks.CertificateUpdateRequest{
				Certificate: test.certificate,
				PrivateKey:  test.privateKey,
			})
			if !reflect.DeepEqual(status, test.wantTerminal) {
				t.Errorf("terminal response = %+v, want %+v", status, test.wantTerminal)
			}
			if test.wantError == "" && err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if test.wantError != "" {
				var operationError *networks.OperationError
				if !errors.As(err, &operationError) || !strings.Contains(err.Error(), test.wantError) || operationError.UpdateID != test.updateID || operationError.Message != test.wantError {
					t.Fatalf("error = %v, want OperationError containing %q", err, test.wantError)
				}
			}

			requests := server.Requests()
			if len(requests) != 1+len(test.polls) {
				t.Fatalf("request count = %d, want %d", len(requests), 1+len(test.polls))
			}
			assertSubmitWire(t, requests[0], test.submitURI, test.token, test.certificate, test.privateKey)
			for index, request := range requests[1:] {
				if request.Method != http.MethodGet || request.RequestURI != test.pollURI {
					t.Errorf("poll %d wire = %s %s", index, request.Method, request.RequestURI)
				}
				assertCommonHeaders(t, request, test.token)
				if len(request.Body) != 0 {
					t.Errorf("poll %d body = %q, want empty", index, request.Body)
				}
				if request.Header.Get("Content-Type") != "" {
					t.Errorf("poll %d Content-Type = %q, want absent", index, request.Header.Get("Content-Type"))
				}
			}
		})
	}
}

func assertSubmitWire(t *testing.T, request mockvcf.Request, requestURI, token, certificate, privateKey string) {
	t.Helper()
	if request.Method != http.MethodPut || request.RequestURI != requestURI {
		t.Errorf("submit wire = %s %s", request.Method, request.RequestURI)
	}
	assertCommonHeaders(t, request, token)
	if request.Header.Get("Content-Type") != "application/json" {
		t.Errorf("submit Content-Type = %q", request.Header.Get("Content-Type"))
	}
	var body map[string]any
	if err := json.Unmarshal(request.Body, &body); err != nil {
		t.Fatalf("submit body is not JSON: %v", err)
	}
	wantBody := map[string]any{"certificate": certificate, "private_key": privateKey}
	if !reflect.DeepEqual(body, wantBody) {
		t.Errorf("submit body = %s, want exactly certificate and private_key", request.Body)
	}
	if _, present := body["chain"]; present || bytes.Contains(request.Body, []byte(`"chain"`)) {
		t.Errorf("unset optional chain was serialized: %s", request.Body)
	}
}

func assertCommonHeaders(t *testing.T, request mockvcf.Request, token string) {
	t.Helper()
	if request.Header.Get("Authorization") != "NetworkInsight "+token {
		t.Errorf("Authorization = %q", request.Header.Get("Authorization"))
	}
	if request.Header.Get("Accept") != "application/json" {
		t.Errorf("Accept = %q", request.Header.Get("Accept"))
	}
}

func TestMockServesOnlyContractOperations(t *testing.T) {
	server := mockvcf.New(mockvcf.Script{})
	defer server.Close()
	client := server.Client()

	tests := []struct {
		method string
		path   string
	}{
		{http.MethodGet, "/api/ni/search"},
		{http.MethodPost, "/api/ni/settings/certificates/proxy_register.crt"},
		{http.MethodPut, "/api/ni/settings/certificates/proxy_register.crt?force=true"},
		{http.MethodGet, "/api/ni/settings/certificates/proxy_register.crt"},
		{http.MethodGet, "/api/ni/settings/certificates/status/"},
		{http.MethodGet, "/api/ni/settings/certificates/status/update-42/extra"},
	}
	for _, test := range tests {
		request, err := http.NewRequest(test.method, server.URL()+test.path, nil)
		if err != nil {
			t.Fatal(err)
		}
		response, err := client.Do(request)
		if err != nil {
			t.Fatal(err)
		}
		_ = response.Body.Close()
		if response.StatusCode != http.StatusNotFound {
			t.Errorf("%s %s status = %d, want 404", test.method, test.path, response.StatusCode)
		}
	}
}

func TestMockRequestLogIsConcurrentAndDetached(t *testing.T) {
	server := mockvcf.New(mockvcf.Script{})
	defer server.Close()
	client := server.Client()

	const requestCount = 32
	errorsSeen := make(chan error, requestCount)
	var wait sync.WaitGroup
	for index := 0; index < requestCount; index++ {
		wait.Add(1)
		go func(index int) {
			defer wait.Done()
			request, err := http.NewRequest(http.MethodPost, fmt.Sprintf("%s/uncontracted/%d", server.URL(), index), strings.NewReader("fixture"))
			if err != nil {
				errorsSeen <- err
				return
			}
			request.Header.Set("X-Fixture", "original")
			response, err := client.Do(request)
			if err != nil {
				errorsSeen <- err
				return
			}
			_ = response.Body.Close()
			if response.StatusCode != http.StatusNotFound {
				errorsSeen <- fmt.Errorf("request %d status = %d", index, response.StatusCode)
			}
		}(index)
	}
	for index := 0; index < requestCount; index++ {
		_ = server.Requests()
	}
	wait.Wait()
	close(errorsSeen)
	for err := range errorsSeen {
		t.Error(err)
	}

	snapshot := server.Requests()
	if len(snapshot) != requestCount {
		t.Fatalf("request log count = %d, want %d", len(snapshot), requestCount)
	}
	snapshot[0].Header.Set("X-Fixture", "mutated")
	snapshot[0].Body[0] = 'X'
	fresh := server.Requests()
	if fresh[0].Header.Get("X-Fixture") != "original" || string(fresh[0].Body) != "fixture" {
		t.Fatalf("request log snapshot aliases server state: %+v", fresh[0])
	}
}

func TestNetworksIncludesTableDrivenTerminalOutcomeTest(t *testing.T) {
	files, err := filepath.Glob(filepath.Join("..", "networks", "*_test.go"))
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range files {
		parsed, err := parser.ParseFile(token.NewFileSet(), name, nil, 0)
		if err != nil {
			t.Fatalf("parse %s: %v", name, err)
		}
		var fileHasSuccess, fileHasFailure bool
		ast.Inspect(parsed, func(node ast.Node) bool {
			switch expression := node.(type) {
			case *ast.SelectorExpr:
				fileHasSuccess = fileHasSuccess || expression.Sel.Name == "StatusSuccess"
				fileHasFailure = fileHasFailure || expression.Sel.Name == "StatusFailed"
			case *ast.BasicLit:
				value := strings.Trim(expression.Value, "`\"")
				fileHasSuccess = fileHasSuccess || value == "SUCCESS"
				fileHasFailure = fileHasFailure || value == "FAILED"
			}
			return true
		})
		for _, declaration := range parsed.Decls {
			function, ok := declaration.(*ast.FuncDecl)
			if !ok || !strings.HasPrefix(function.Name.Name, "Test") || function.Body == nil {
				continue
			}
			var hasRange, callsIntegration bool
			ast.Inspect(function.Body, func(node ast.Node) bool {
				switch expression := node.(type) {
				case *ast.RangeStmt:
					hasRange = true
				case *ast.SelectorExpr:
					if expression.Sel.Name == "UpdateCertificateAndWait" {
						callsIntegration = true
					}
				}
				return true
			})
			if hasRange && fileHasSuccess && fileHasFailure && callsIntegration {
				return
			}
		}
	}
	t.Fatal("networks must include a table-driven test that calls UpdateCertificateAndWait for SUCCESS and FAILED outcomes")
}
