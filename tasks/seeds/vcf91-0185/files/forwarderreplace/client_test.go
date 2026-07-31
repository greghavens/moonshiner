package forwarderreplace

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"sync/atomic"
	"testing"

	"example.com/vcf-forwarder-replace/internal/contractmock"
)

const (
	contractFile = "../docs/contract.json"
	testToken    = "verifier-token-0185"
)

func TestContractProvenanceAndFocusedOperations(t *testing.T) {
	t.Parallel()
	var sources struct {
		Repository  string   `json:"repository"`
		License     string   `json:"license"`
		CommitSHA   string   `json:"commit_sha"`
		SpecBlobSHA string   `json:"spec_blob_sha"`
		SpecPath    string   `json:"spec_path"`
		SourceURL   string   `json:"source_url"`
		Operations  []string `json:"operationIds"`
	}
	readJSON(t, "../docs/official_sources.json", &sources)
	if sources.Repository != "https://github.com/vmware/vcf-api-specs" ||
		sources.License != "Apache-2.0" ||
		sources.CommitSHA != "3949fc33339fc5ea1b77eadb258f1cf49aa88e26" ||
		sources.SpecBlobSHA != "4ada16fa39ec345674de4126174de94ea70d23a0" ||
		sources.SpecPath != "specifications/vcf-operations/log-management-openapi.json" {
		t.Fatalf("official source pin changed: %+v", sources)
	}
	if !strings.Contains(sources.SourceURL, sources.CommitSHA+"/"+sources.SpecPath) {
		t.Fatalf("source URL is not immutable: %q", sources.SourceURL)
	}
	wantOperations := []string{"createLogForwarder", "deleteLogForwarder"}
	if !reflect.DeepEqual(sources.Operations, wantOperations) {
		t.Fatalf("official operationIds = %v, want %v", sources.Operations, wantOperations)
	}

	var contract struct {
		OpenAPI string `json:"openapi"`
		Info    struct {
			Version string `json:"version"`
		} `json:"info"`
		Source struct {
			CommitSHA string `json:"commit_sha"`
			SpecPath  string `json:"spec_path"`
		} `json:"x-official-source"`
		Paths map[string]map[string]struct {
			OperationID string `json:"operationId"`
		} `json:"paths"`
	}
	readJSON(t, contractFile, &contract)
	if contract.OpenAPI != "3.0.1" || contract.Info.Version != "9.1.0.0" ||
		contract.Source.CommitSHA != sources.CommitSHA || contract.Source.SpecPath != sources.SpecPath {
		t.Fatalf("contract source/version pin changed: %+v", contract)
	}
	var gotOperations []string
	for _, pathItem := range contract.Paths {
		for _, operation := range pathItem {
			if operation.OperationID != "" {
				gotOperations = append(gotOperations, operation.OperationID)
			}
		}
	}
	sort.Strings(gotOperations)
	if !reflect.DeepEqual(gotOperations, wantOperations) {
		t.Fatalf("contract operations = %v, want exactly %v", gotOperations, wantOperations)
	}
	if contract.Paths["/api/v2/logs/forwarders"]["post"].OperationID != "createLogForwarder" ||
		contract.Paths["/api/v2/logs/forwarders/{id}"]["delete"].OperationID != "deleteLogForwarder" {
		t.Fatal("contract method/path projection changed")
	}

	raw, err := os.ReadFile(filepath.Clean(contractFile))
	if err != nil {
		t.Fatal(err)
	}
	propertyOrder := []string{
		`"certificate"`, `"connectionRefreshInterval"`, `"constraints"`, `"enabled"`,
		`"forwardComplementaryFields"`, `"host"`, `"id"`, `"name"`, `"port"`,
		`"protocol"`, `"sslEnabled"`, `"tags"`, `"transportProtocol"`, `"workerCount"`,
	}
	schemaStart := bytes.Index(raw, []byte(`"LogForwarder"`))
	if schemaStart < 0 {
		t.Fatal("LogForwarder schema missing")
	}
	cursor := schemaStart
	for _, property := range propertyOrder {
		next := bytes.Index(raw[cursor:], []byte(property))
		if next < 0 {
			t.Fatalf("schema property %s missing or out of source order", property)
		}
		cursor += next + len(property)
	}
	idStart := bytes.Index(raw[schemaStart:], []byte(`"id"`))
	if idStart < 0 || !bytes.Contains(raw[schemaStart+idStart:schemaStart+idStart+100], []byte(`"readOnly": true`)) {
		t.Fatal("LogForwarder.id is no longer marked readOnly")
	}
}

func TestReplaceReportsCreateWhenLaterDeleteFailsAndPreservesWire(t *testing.T) {
	t.Parallel()
	createResponse := `{"certificate":"","constraints":{},"enabled":false,"host":"<edge>.corp","id":"new-7","name":"replacement","port":0,"protocol":"SYSLOG","sslEnabled":false,"tags":{},"transportProtocol":"TCP","workerCount":0}`
	server := startMock(t, contractmock.Options{
		ExpectedToken: testToken,
		CreateBody:    []byte(createResponse),
		DeleteStatus:  http.StatusNotFound,
	})
	client, err := NewClient(Config{BaseURL: server.URL(), Token: testToken, HTTPClient: server.Client()})
	if err != nil {
		t.Fatal(err)
	}

	empty := ""
	constraints := json.RawMessage(`{}`)
	disabled := false
	host := "<edge>.corp"
	name := "replacement"
	zero := int32(0)
	protocol := "SYSLOG"
	emptyTags := map[string]string{}
	transport := "TCP"
	input := LogForwarderCreate{
		Certificate:       &empty,
		Constraints:       &constraints,
		Enabled:           &disabled,
		Host:              &host,
		Name:              &name,
		Port:              &zero,
		Protocol:          &protocol,
		SSLEnabled:        &disabled,
		Tags:              &emptyTags,
		TransportProtocol: &transport,
		WorkerCount:       &zero,
	}
	result, callErr := client.ReplaceLogForwarder(context.Background(), "old/edge ?#%", input)

	wantCreate := StepOutcome{
		OperationID: "createLogForwarder",
		Attempted:   true,
		Succeeded:   true,
		StatusCode:  http.StatusCreated,
	}
	wantDelete := StepOutcome{
		OperationID: "deleteLogForwarder",
		Attempted:   true,
		Succeeded:   false,
		StatusCode:  http.StatusNotFound,
	}
	if result.Created == nil || result.Created.ID != "new-7" {
		t.Fatalf("created result lost after later failure: %+v", result.Created)
	}
	if result.Create != wantCreate || result.Delete != wantDelete {
		t.Fatalf("outcomes = create %+v delete %+v; want %+v and %+v", result.Create, result.Delete, wantCreate, wantDelete)
	}
	assertStepError(t, callErr, "deleteLogForwarder", StepDelete, KindHTTP, http.StatusNotFound)

	records, err := server.ReadLog()
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 2 {
		t.Fatalf("request count = %d, want 2: %+v", len(records), records)
	}
	expectedBody := `{"certificate":"","constraints":{},"enabled":false,"host":"<edge>.corp","name":"replacement","port":0,"protocol":"SYSLOG","sslEnabled":false,"tags":{},"transportProtocol":"TCP","workerCount":0}`
	assertRecord(t, records[0], "createLogForwarder", http.MethodPost, "/api/v2/logs/forwarders", expectedBody)
	assertOneHeader(t, records[0].Headers, "Accept", "application/json")
	assertOneHeader(t, records[0].Headers, "Content-Type", "application/json")
	assertOneHeader(t, records[0].Headers, "X-JWT-Token", testToken)
	for _, omitted := range []string{"connectionRefreshInterval", "forwardComplementaryFields", `"id"`} {
		if strings.Contains(records[0].Body, omitted) {
			t.Fatalf("unset/read-only field %q was sent in %s", omitted, records[0].Body)
		}
	}
	assertRecord(t, records[1], "deleteLogForwarder", http.MethodDelete, "/api/v2/logs/forwarders/old%2Fedge%20%3F%23%25", "")
	assertOneHeader(t, records[1].Headers, "Accept", "application/json")
	assertOneHeader(t, records[1].Headers, "X-JWT-Token", testToken)
	if values := records[1].Headers.Values("Content-Type"); len(values) != 0 {
		t.Fatalf("DELETE Content-Type values = %q, want absent", values)
	}
}

func TestWorkflowOutcomeMatrix(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name          string
		options       contractmock.Options
		wantCreate    StepOutcome
		wantDelete    StepOutcome
		wantErrorStep Step
		wantKind      ErrorKind
		wantStatus    int
		wantRequests  int
	}{
		{
			name: "both succeed",
			options: contractmock.Options{
				CreateBody: []byte(`{"id":"new-ok"}`),
			},
			wantCreate:   outcome("createLogForwarder", true, true, http.StatusCreated),
			wantDelete:   outcome("deleteLogForwarder", true, true, http.StatusNoContent),
			wantRequests: 2,
		},
		{
			name: "create HTTP failure stops workflow",
			options: contractmock.Options{
				CreateStatus: http.StatusUnprocessableEntity,
			},
			wantCreate:    outcome("createLogForwarder", true, false, http.StatusUnprocessableEntity),
			wantDelete:    outcome("deleteLogForwarder", false, false, 0),
			wantErrorStep: StepCreate,
			wantKind:      KindHTTP,
			wantStatus:    http.StatusUnprocessableEntity,
			wantRequests:  1,
		},
		{
			name: "delete HTTP failure keeps create",
			options: contractmock.Options{
				CreateBody:   []byte(`{"id":"new-kept"}`),
				DeleteStatus: http.StatusForbidden,
			},
			wantCreate:    outcome("createLogForwarder", true, true, http.StatusCreated),
			wantDelete:    outcome("deleteLogForwarder", true, false, http.StatusForbidden),
			wantErrorStep: StepDelete,
			wantKind:      KindHTTP,
			wantStatus:    http.StatusForbidden,
			wantRequests:  2,
		},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			test.options.ExpectedToken = testToken
			server := startMock(t, test.options)
			client, err := NewClient(Config{BaseURL: server.URL(), Token: testToken, HTTPClient: server.Client()})
			if err != nil {
				t.Fatal(err)
			}
			result, callErr := client.ReplaceLogForwarder(context.Background(), "old-1", LogForwarderCreate{})
			if result.Create != test.wantCreate || result.Delete != test.wantDelete {
				t.Fatalf("outcomes = %+v %+v, want %+v %+v", result.Create, result.Delete, test.wantCreate, test.wantDelete)
			}
			if test.wantErrorStep == "" {
				if callErr != nil {
					t.Fatalf("unexpected error: %v", callErr)
				}
			} else {
				wantOperation := "createLogForwarder"
				if test.wantErrorStep == StepDelete {
					wantOperation = "deleteLogForwarder"
				}
				assertStepError(t, callErr, wantOperation, test.wantErrorStep, test.wantKind, test.wantStatus)
				var stepErr *Error
				_ = errors.As(callErr, &stepErr)
				if stepErr.OperationID != wantOperation {
					t.Fatalf("error operation = %q, want %q", stepErr.OperationID, wantOperation)
				}
			}
			records, err := server.ReadLog()
			if err != nil {
				t.Fatal(err)
			}
			if len(records) != test.wantRequests {
				t.Fatalf("request count = %d, want %d", len(records), test.wantRequests)
			}
			if test.wantCreate.Succeeded && (result.Created == nil || result.Created.ID == "") {
				t.Fatalf("successful create was not reported: %+v", result)
			}
		})
	}
}

func TestResponseValidationTable(t *testing.T) {
	t.Parallel()
	oversized := `{"id":"` + strings.Repeat("x", (1<<20)+1) + `"}`
	createCases := []struct {
		name        string
		status      int
		contentType string
		body        string
		wantKind    ErrorKind
	}{
		{name: "null", status: 201, contentType: "application/json", body: "null", wantKind: KindProtocol},
		{name: "array", status: 201, contentType: "application/json", body: "[]", wantKind: KindProtocol},
		{name: "scalar", status: 201, contentType: "application/json", body: `"value"`, wantKind: KindProtocol},
		{name: "malformed", status: 201, contentType: "application/json", body: "{", wantKind: KindProtocol},
		{name: "trailing data", status: 201, contentType: "application/json", body: `{"id":"x"} {}`, wantKind: KindProtocol},
		{name: "wrong media type", status: 201, contentType: "text/plain", body: `{"id":"x"}`, wantKind: KindProtocol},
		{name: "oversized", status: 201, contentType: "application/json", body: oversized, wantKind: KindProtocol},
		{name: "received non-success", status: 500, contentType: "application/json", body: `{"errorMessage":"do not expose"}`, wantKind: KindHTTP},
	}
	for _, test := range createCases {
		test := test
		t.Run("create "+test.name, func(t *testing.T) {
			var closed atomic.Bool
			transport := sequenceTransport{responses: []scriptedResponse{{
				status: test.status, contentType: test.contentType, body: test.body, closed: &closed,
			}}}
			client := mustClient(t, &http.Client{Transport: &transport})
			result, err := client.ReplaceLogForwarder(context.Background(), "old", LogForwarderCreate{})
			assertStepError(t, err, "createLogForwarder", StepCreate, test.wantKind, test.status)
			if !closed.Load() {
				t.Fatal("create response body was not closed")
			}
			want := outcome("createLogForwarder", true, false, test.status)
			if result.Create != want || result.Delete.Attempted || result.Created != nil {
				t.Fatalf("result = %+v, want failed create and unattempted delete", result)
			}
			if strings.Contains(err.Error(), "do not expose") {
				t.Fatal("response body leaked through error")
			}
		})
	}

	t.Run("delete nonempty 204", func(t *testing.T) {
		var createClosed, deleteClosed atomic.Bool
		transport := sequenceTransport{responses: []scriptedResponse{
			{status: 201, contentType: "application/json; charset=utf-8", body: `{"id":"kept"}`, closed: &createClosed},
			{status: 204, body: "unexpected", closed: &deleteClosed},
		}}
		client := mustClient(t, &http.Client{Transport: &transport})
		result, err := client.ReplaceLogForwarder(context.Background(), "old", LogForwarderCreate{})
		assertStepError(t, err, "deleteLogForwarder", StepDelete, KindProtocol, http.StatusNoContent)
		if result.Created == nil || result.Created.ID != "kept" || !result.Create.Succeeded ||
			result.Delete != outcome("deleteLogForwarder", true, false, http.StatusNoContent) {
			t.Fatalf("partial result = %+v", result)
		}
		if !createClosed.Load() || !deleteClosed.Load() {
			t.Fatal("one or more response bodies were not closed")
		}
	})
}

func TestTransportFailureAndContextRemainTruthful(t *testing.T) {
	t.Parallel()
	sensitiveTransportText := "wire failed with verifier-token-0185 and private diagnostics"
	transport := sequenceTransport{responses: []scriptedResponse{
		{status: 201, contentType: "application/json", body: `{"id":"kept-after-transport"}`},
		{err: errors.New(sensitiveTransportText)},
	}}
	client := mustClient(t, &http.Client{Transport: &transport})
	result, err := client.ReplaceLogForwarder(context.Background(), "old", LogForwarderCreate{})
	assertStepError(t, err, "deleteLogForwarder", StepDelete, KindTransport, 0)
	if result.Created == nil || result.Created.ID != "kept-after-transport" ||
		result.Create != outcome("createLogForwarder", true, true, 201) ||
		result.Delete != outcome("deleteLogForwarder", true, false, 0) {
		t.Fatalf("transport partial result = %+v", result)
	}
	if strings.Contains(err.Error(), sensitiveTransportText) || strings.Contains(err.Error(), testToken) {
		t.Fatalf("transport details leaked: %v", err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	cancelClient := mustClient(t, &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		return nil, request.Context().Err()
	})})
	_, err = cancelClient.ReplaceLogForwarder(ctx, "old", LogForwarderCreate{})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("errors.Is(context.Canceled) = false for %v", err)
	}
	assertStepError(t, err, "createLogForwarder", StepCreate, KindTransport, 0)
}

func TestValidationBeforeIO(t *testing.T) {
	t.Parallel()
	var calls atomic.Int32
	countingClient := &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
		calls.Add(1)
		return nil, errors.New("unexpected traffic")
	})}
	validConfig := Config{BaseURL: "http://127.0.0.1:7788", Token: "token", HTTPClient: countingClient}

	configCases := []struct {
		name   string
		mutate func(*Config)
	}{
		{name: "relative URL", mutate: func(config *Config) { config.BaseURL = "/api" }},
		{name: "unsupported scheme", mutate: func(config *Config) { config.BaseURL = "ftp://127.0.0.1" }},
		{name: "userinfo", mutate: func(config *Config) { config.BaseURL = "http://user@127.0.0.1" }},
		{name: "non-root path", mutate: func(config *Config) { config.BaseURL = "http://127.0.0.1/base" }},
		{name: "query", mutate: func(config *Config) { config.BaseURL = "http://127.0.0.1?x=1" }},
		{name: "bare query", mutate: func(config *Config) { config.BaseURL = "http://127.0.0.1?" }},
		{name: "fragment", mutate: func(config *Config) { config.BaseURL = "http://127.0.0.1/#x" }},
		{name: "blank token", mutate: func(config *Config) { config.Token = " \t" }},
		{name: "token whitespace", mutate: func(config *Config) { config.Token = " token" }},
		{name: "token newline", mutate: func(config *Config) { config.Token = "tok\nen" }},
	}
	for _, test := range configCases {
		test := test
		t.Run(test.name, func(t *testing.T) {
			config := validConfig
			test.mutate(&config)
			if _, err := NewClient(config); err == nil {
				t.Fatal("NewClient accepted invalid config")
			}
		})
	}

	client, err := NewClient(validConfig)
	if err != nil {
		t.Fatal(err)
	}
	methodCases := []struct {
		name string
		ctx  context.Context
		id   string
		body LogForwarderCreate
	}{
		{name: "nil context", ctx: nil, id: "old"},
		{name: "empty id", ctx: context.Background(), id: ""},
		{name: "invalid raw JSON", ctx: context.Background(), id: "old", body: LogForwarderCreate{Constraints: rawPointer(`{`)}},
	}
	for _, test := range methodCases {
		test := test
		t.Run(test.name, func(t *testing.T) {
			if _, err := client.ReplaceLogForwarder(test.ctx, test.id, test.body); err == nil {
				t.Fatal("ReplaceLogForwarder accepted invalid input")
			}
		})
	}
	if got := calls.Load(); got != 0 {
		t.Fatalf("validation made %d request(s)", got)
	}
}

func TestRedirectsDisabledAndCallerClientUnchanged(t *testing.T) {
	t.Parallel()
	server := startMock(t, contractmock.Options{
		ExpectedToken:    testToken,
		CreateStatus:     http.StatusTemporaryRedirect,
		RedirectLocation: "http://127.0.0.1:1/api/v2/logs/forwarders",
	})
	var callerRedirects atomic.Int32
	callerCheck := func(*http.Request, []*http.Request) error {
		callerRedirects.Add(1)
		return nil
	}
	caller := server.Client()
	caller.CheckRedirect = callerCheck
	client, err := NewClient(Config{BaseURL: server.URL(), Token: testToken, HTTPClient: caller})
	if err != nil {
		t.Fatal(err)
	}
	_, callErr := client.ReplaceLogForwarder(context.Background(), "old", LogForwarderCreate{})
	assertStepError(t, callErr, "createLogForwarder", StepCreate, KindHTTP, http.StatusTemporaryRedirect)
	if caller.CheckRedirect == nil || reflect.ValueOf(caller.CheckRedirect).Pointer() != reflect.ValueOf(callerCheck).Pointer() {
		t.Fatal("NewClient mutated caller-owned CheckRedirect")
	}
	if callerRedirects.Load() != 0 {
		t.Fatal("client followed redirect through caller callback")
	}
	records, err := server.ReadLog()
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 1 {
		t.Fatalf("redirect caused %d requests, want 1", len(records))
	}
}

func TestNilHTTPClientUsesIndependentDefaultCopy(t *testing.T) {
	t.Parallel()
	before := http.DefaultClient.CheckRedirect
	client, err := NewClient(Config{
		BaseURL: "http://127.0.0.1:7788/",
		Token:   testToken,
	})
	if err != nil {
		t.Fatal(err)
	}
	if client.httpClient == http.DefaultClient || client.httpClient.CheckRedirect == nil {
		t.Fatal("nil HTTPClient did not produce an independent redirect-disabled copy")
	}
	if client.baseURL != "http://127.0.0.1:7788" {
		t.Fatalf("normalized base URL = %q", client.baseURL)
	}
	if (before == nil) != (http.DefaultClient.CheckRedirect == nil) {
		t.Fatal("NewClient mutated http.DefaultClient")
	}
	if before != nil &&
		reflect.ValueOf(before).Pointer() != reflect.ValueOf(http.DefaultClient.CheckRedirect).Pointer() {
		t.Fatal("NewClient replaced http.DefaultClient.CheckRedirect")
	}
}

func TestMockRefusesOperationsOutsideContract(t *testing.T) {
	t.Parallel()
	server := startMock(t, contractmock.Options{ExpectedToken: testToken})
	tests := []struct {
		method string
		target string
	}{
		{method: http.MethodGet, target: "/api/v2/logs/forwarders"},
		{method: http.MethodPatch, target: "/api/v2/logs/forwarders/old"},
		{method: http.MethodPost, target: "/api/v2/logs/forwarders/test"},
		{method: http.MethodDelete, target: "/api/v2/logs/forwarders/old?force=true"},
	}
	for _, test := range tests {
		request, err := http.NewRequest(test.method, server.URL()+test.target, nil)
		if err != nil {
			t.Fatal(err)
		}
		response, err := server.Client().Do(request)
		if err != nil {
			t.Fatal(err)
		}
		_, _ = io.Copy(io.Discard, response.Body)
		_ = response.Body.Close()
		if response.StatusCode != http.StatusNotFound {
			t.Fatalf("%s %s status = %d, want 404", test.method, test.target, response.StatusCode)
		}
	}
	records, err := server.ReadLog()
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 0 {
		t.Fatalf("mock logged/served unnamed operations: %+v", records)
	}
}

func startMock(t *testing.T, options contractmock.Options) *contractmock.Server {
	t.Helper()
	options.ContractPath = contractFile
	server, err := contractmock.Start(options)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(server.Close)
	return server
}

func mustClient(t *testing.T, httpClient *http.Client) *Client {
	t.Helper()
	client, err := NewClient(Config{
		BaseURL:    "http://127.0.0.1:7788",
		Token:      testToken,
		HTTPClient: httpClient,
	})
	if err != nil {
		t.Fatal(err)
	}
	return client
}

func outcome(operationID string, attempted, succeeded bool, status int) StepOutcome {
	return StepOutcome{
		OperationID: operationID,
		Attempted:   attempted,
		Succeeded:   succeeded,
		StatusCode:  status,
	}
}

func assertStepError(t *testing.T, err error, operation string, step Step, kind ErrorKind, status int) {
	t.Helper()
	if err == nil {
		t.Fatal("expected error")
	}
	var stepErr *Error
	if !errors.As(err, &stepErr) {
		t.Fatalf("error type = %T, want *Error: %v", err, err)
	}
	if stepErr.OperationID != operation || stepErr.Step != step || stepErr.Kind != kind || stepErr.StatusCode != status {
		t.Fatalf("error = %+v, want operation=%s step=%s kind=%s status=%d", stepErr, operation, step, kind, status)
	}
}

func assertRecord(t *testing.T, record contractmock.RequestRecord, operation, method, requestURI, body string) {
	t.Helper()
	if record.OperationID != operation || record.Method != method || record.RequestURI != requestURI || record.Body != body {
		t.Fatalf("record = %+v, want operation=%s method=%s URI=%s body=%q", record, operation, method, requestURI, body)
	}
}

func assertOneHeader(t *testing.T, header http.Header, name, value string) {
	t.Helper()
	values := header.Values(name)
	if len(values) != 1 || values[0] != value {
		t.Fatalf("%s values = %q, want exactly [%q]", name, values, value)
	}
}

func readJSON(t *testing.T, path string, target any) {
	t.Helper()
	raw, err := os.ReadFile(filepath.Clean(path))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, target); err != nil {
		t.Fatal(err)
	}
}

func rawPointer(value string) *json.RawMessage {
	raw := json.RawMessage(value)
	return &raw
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

type scriptedResponse struct {
	status      int
	contentType string
	body        string
	err         error
	closed      *atomic.Bool
}

type sequenceTransport struct {
	index     atomic.Int32
	responses []scriptedResponse
}

func (transport *sequenceTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	index := int(transport.index.Add(1)) - 1
	if index >= len(transport.responses) {
		return nil, errors.New("unexpected extra request")
	}
	script := transport.responses[index]
	if script.err != nil {
		return nil, script.err
	}
	header := make(http.Header)
	if script.contentType != "" {
		header.Set("Content-Type", script.contentType)
	}
	body := &trackingBody{Reader: strings.NewReader(script.body), closed: script.closed}
	return &http.Response{
		StatusCode: script.status,
		Header:     header,
		Body:       body,
		Request:    request,
	}, nil
}

type trackingBody struct {
	*strings.Reader
	closed *atomic.Bool
}

func (body *trackingBody) Close() error {
	if body.closed != nil {
		body.closed.Store(true)
	}
	return nil
}
