package resize_test

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	resize "vcf91-0113"
	"vcf91-0113/internal/contractmock"
)

const (
	expectedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	expectedSpec   = "specifications/vsphere/openapi/automation/vcenter.yaml"
	contractSHA256 = "48396ac56cb70efd83e1cb49becdae3bf5a92184ac46804483d8e2df68ab61a3"
	sourcesSHA256  = "fef87b0869b6e8edad49530a46970598cebc24434b7fa20ec78cc71c8c594628"
)

type operationSource struct {
	OperationID         string `json:"operationId"`
	Method              string `json:"method"`
	Path                string `json:"path"`
	SpecPathItem        string `json:"specPathItem"`
	RepositoryCommitSHA string `json:"repositoryCommitSha"`
	SpecPath            string `json:"specPath"`
}

func TestProtectedContractProvenance(t *testing.T) {
	assertFileHash(t, "docs/contract.json", contractSHA256)
	assertFileHash(t, "docs/official_sources.json", sourcesSHA256)

	var contract struct {
		Source struct {
			Kind        string `json:"kind"`
			CommitSHA   string `json:"commitSha"`
			SpecPath    string `json:"specPath"`
			SpecBlobSHA string `json:"specBlobSha"`
			License     string `json:"license"`
			OpenAPI     string `json:"openapi"`
			APIVersion  string `json:"apiVersion"`
			BasePath    string `json:"basePath"`
		} `json:"source"`
		SecuritySchemes map[string]struct {
			Type string `json:"type"`
			In   string `json:"in"`
			Name string `json:"name"`
		} `json:"securitySchemes"`
		Operations []operationSource `json:"operations"`
		Schemas    map[string]struct {
			Required   []string `json:"required"`
			Properties map[string]struct {
				Type          string `json:"type"`
				Format        string `json:"format"`
				Required      bool   `json:"required"`
				UnsetBehavior string `json:"unsetBehavior"`
			} `json:"properties"`
		} `json:"schemas"`
	}
	readJSON(t, "docs/contract.json", &contract)

	var sources struct {
		RepositoryCommitSHA string            `json:"repositoryCommitSha"`
		SpecPath            string            `json:"specPath"`
		SpecBlobSHA         string            `json:"specBlobSha"`
		License             string            `json:"license"`
		OperationIDs        []string          `json:"operationIds"`
		Operations          []operationSource `json:"operations"`
		Derivation          string            `json:"derivation"`
	}
	readJSON(t, "docs/official_sources.json", &sources)

	if contract.Source.Kind != "pinned-openapi-specification" ||
		contract.Source.CommitSHA != expectedCommit ||
		sources.RepositoryCommitSHA != expectedCommit ||
		contract.Source.SpecPath != expectedSpec ||
		sources.SpecPath != expectedSpec ||
		contract.Source.SpecBlobSHA != sources.SpecBlobSHA ||
		contract.Source.SpecBlobSHA != "8028b0824c4ff3503d05f44814f967938a795c40" {
		t.Fatalf("incorrect pinned source provenance: contract=%+v sources=%+v",
			contract.Source, sources)
	}
	if contract.Source.License != "Apache-2.0" ||
		sources.License != "Apache-2.0" ||
		contract.Source.OpenAPI != "3.0.3" ||
		contract.Source.APIVersion != "9.1.0.0" ||
		contract.Source.BasePath != "/api" {
		t.Fatalf("incorrect version/license projection: contract=%+v sources=%+v",
			contract.Source, sources)
	}
	if !strings.Contains(sources.Derivation, "OpenAPI YAML specification") ||
		!strings.Contains(sources.Derivation, "No rendered documentation page") {
		t.Fatalf("source derivation is not explicit: %q", sources.Derivation)
	}

	wantOperations := []operationSource{
		{
			OperationID:  resize.CPUUpdateOperation,
			Method:       http.MethodPatch,
			Path:         "/api/vcenter/vm/{vm}/hardware/cpu",
			SpecPathItem: "/vcenter/vm/{vm}/hardware/cpu",
		},
		{
			OperationID:  resize.MemoryUpdateOperation,
			Method:       http.MethodPatch,
			Path:         "/api/vcenter/vm/{vm}/hardware/memory",
			SpecPathItem: "/vcenter/vm/{vm}/hardware/memory",
		},
		{
			OperationID:  resize.PowerStartOperation,
			Method:       http.MethodPost,
			Path:         "/api/vcenter/vm/{vm}/power?action=start",
			SpecPathItem: "/vcenter/vm/{vm}/power?action=start",
		},
	}
	if !reflect.DeepEqual(contract.Operations, wantOperations) {
		t.Fatalf("contract operations mismatch\n got: %#v\nwant: %#v",
			contract.Operations, wantOperations)
	}
	wantIDs := []string{
		resize.CPUUpdateOperation,
		resize.MemoryUpdateOperation,
		resize.PowerStartOperation,
	}
	if !reflect.DeepEqual(sources.OperationIDs, wantIDs) ||
		len(sources.Operations) != len(wantOperations) {
		t.Fatalf("official operation sources mismatch: ids=%v operations=%#v",
			sources.OperationIDs, sources.Operations)
	}
	for index, operation := range sources.Operations {
		if operation.OperationID != wantIDs[index] ||
			operation.Method != wantOperations[index].Method ||
			operation.Path != wantOperations[index].SpecPathItem ||
			operation.RepositoryCommitSHA != expectedCommit ||
			operation.SpecPath != expectedSpec {
			t.Fatalf("operation source %d is not independently pinned: %#v", index, operation)
		}
	}

	security := contract.SecuritySchemes["api_key_auth"]
	if security.Type != "apiKey" ||
		security.In != "header" ||
		security.Name != "vmware-api-session-id" {
		t.Fatalf("security projection mismatch: %+v", security)
	}
	cpu := contract.Schemas["Vcenter.Vm.Hardware.Cpu.UpdateSpec"]
	memory := contract.Schemas["Vcenter.Vm.Hardware.Memory.UpdateSpec"]
	assertOptionalProperties(t, cpu.Required, cpu.Properties, []string{
		"cores_per_socket", "count", "hot_add_enabled", "hot_remove_enabled",
	})
	assertOptionalProperties(t, memory.Required, memory.Properties, []string{
		"hot_add_enabled", "size_mib",
	})
	if cpu.Properties["count"].Format != "int64" ||
		memory.Properties["size_mib"].Format != "int64" {
		t.Fatalf("integer schema projection lost int64 formats")
	}
}

func TestResizeAndStartReportsLaterPowerFailureAndExactWire(t *testing.T) {
	server := newServer(t, contractmock.Plan{})
	runtime := server.Runtime()
	client := newClient(t, server)

	report, err := client.ResizeAndStart(
		context.Background(),
		runtime.VM,
		runtime.CPUCount,
		runtime.MemoryMiB,
	)
	var apiError *resize.APIError
	if !errors.As(err, &apiError) {
		t.Fatalf("error = %T %v, want *APIError", err, err)
	}
	if apiError.OperationID != resize.PowerStartOperation ||
		apiError.StatusCode != http.StatusServiceUnavailable ||
		apiError.ErrorType != "SERVICE_UNAVAILABLE" ||
		apiError.Message != runtime.FailureMessage {
		t.Fatalf("APIError lost contract data: %#v", apiError)
	}
	for _, secret := range []string{
		runtime.SessionToken,
		runtime.FailureMessage,
		"SERVICE_UNAVAILABLE",
	} {
		if strings.Contains(apiError.Error(), secret) {
			t.Fatalf("APIError text exposed %q: %q", secret, apiError.Error())
		}
	}

	wantReport := resize.ResizeReport{
		VM:                 runtime.VM,
		OverallState:       "FAILED",
		CompletedStepCount: 2,
		FailedOperationID:  resize.PowerStartOperation,
		Steps: []resize.StepResult{
			{
				Name:        "Cpu",
				OperationID: resize.CPUUpdateOperation,
				State:       "SUCCEEDED",
				HTTPStatus:  http.StatusNoContent,
			},
			{
				Name:        "Memory",
				OperationID: resize.MemoryUpdateOperation,
				State:       "SUCCEEDED",
				HTTPStatus:  http.StatusNoContent,
			},
			{
				Name:        "PowerStart",
				OperationID: resize.PowerStartOperation,
				State:       "FAILED",
				HTTPStatus:  http.StatusServiceUnavailable,
				ErrorType:   "SERVICE_UNAVAILABLE",
				Message:     runtime.FailureMessage,
			},
		},
	}
	if !reflect.DeepEqual(report, wantReport) {
		t.Fatalf("partial-failure report mismatch\n got: %#v\nwant: %#v", report, wantReport)
	}

	requests := server.Requests()
	encodedVM := url.PathEscape(runtime.VM)
	wireCases := []struct {
		operationID string
		method      string
		requestURI  string
		query       string
		contentType string
		body        string
		members     []string
		status      int
	}{
		{
			operationID: resize.CPUUpdateOperation,
			method:      http.MethodPatch,
			requestURI:  "/api/vcenter/vm/" + encodedVM + "/hardware/cpu",
			contentType: "application/json",
			body:        `{"count":` + integerString(runtime.CPUCount) + `}`,
			members:     []string{"count"},
			status:      http.StatusNoContent,
		},
		{
			operationID: resize.MemoryUpdateOperation,
			method:      http.MethodPatch,
			requestURI:  "/api/vcenter/vm/" + encodedVM + "/hardware/memory",
			contentType: "application/json",
			body:        `{"size_mib":` + integerString(runtime.MemoryMiB) + `}`,
			members:     []string{"size_mib"},
			status:      http.StatusNoContent,
		},
		{
			operationID: resize.PowerStartOperation,
			method:      http.MethodPost,
			requestURI:  "/api/vcenter/vm/" + encodedVM + "/power?action=start",
			query:       "action=start",
			status:      http.StatusServiceUnavailable,
		},
	}
	if len(requests) != len(wireCases) {
		t.Fatalf("request count = %d, want %d: %#v", len(requests), len(wireCases), requests)
	}
	for index, test := range wireCases {
		t.Run("wire_"+test.operationID, func(t *testing.T) {
			request := requests[index]
			if request.OperationID != test.operationID ||
				request.Method != test.method ||
				request.RequestURI != test.requestURI ||
				request.RawQuery != test.query ||
				request.Status != test.status {
				t.Fatalf("request routing mismatch: %#v", request)
			}
			if request.Header.Get("Accept") != "application/json" ||
				len(request.Header.Values("Accept")) != 1 ||
				request.Header.Get("vmware-api-session-id") != runtime.SessionToken ||
				len(request.Header.Values("vmware-api-session-id")) != 1 ||
				len(request.Header.Values("Authorization")) != 0 {
				t.Fatalf("request authentication/media headers mismatch: %#v", request.Header)
			}
			if request.Header.Get("Content-Type") != test.contentType ||
				len(request.Header.Values("Content-Type")) != boolCount(test.contentType != "") {
				t.Fatalf("request Content-Type mismatch: %#v", request.Header)
			}
			if string(request.Body) != test.body ||
				request.ContentLength != int64(len(test.body)) ||
				len(request.TransferEncoding) != 0 {
				t.Fatalf("request body wire shape mismatch: length=%d transfer=%v body=%q",
					request.ContentLength, request.TransferEncoding, request.Body)
			}
			if test.members != nil {
				assertJSONMembers(t, request.Body, test.members)
			}
		})
	}
}

func TestResizeAndStartAllSuccess(t *testing.T) {
	server := newServer(t, contractmock.Plan{Replies: map[string]contractmock.Reply{
		contractmock.PowerStart: {Status: http.StatusNoContent},
	}})
	runtime := server.Runtime()
	client := newClient(t, server)
	report, err := client.ResizeAndStart(
		context.Background(), runtime.VM, runtime.CPUCount, runtime.MemoryMiB,
	)
	if err != nil {
		t.Fatalf("ResizeAndStart: %v", err)
	}
	if report.OverallState != "SUCCEEDED" ||
		report.CompletedStepCount != 3 ||
		report.FailedOperationID != "" ||
		len(report.Steps) != 3 {
		t.Fatalf("all-success report mismatch: %#v", report)
	}
	for _, step := range report.Steps {
		if step.State != "SUCCEEDED" ||
			step.HTTPStatus != http.StatusNoContent ||
			step.ErrorType != "" ||
			step.Message != "" {
			t.Fatalf("success step contains failure data: %#v", step)
		}
	}
}

func TestResizeAndStartStopsAfterFirstAPIFailure(t *testing.T) {
	errorBody := contractmock.VAPIError{
		ErrorType: "SERVICE_UNAVAILABLE",
		Messages: []contractmock.LocalizableMessage{{
			Args:           []string{},
			DefaultMessage: "planned update failure",
			ID:             "test.update.failure",
		}},
	}
	tests := []struct {
		name          string
		failed        string
		wantRequests  int
		wantCompleted int
	}{
		{name: "cpu", failed: contractmock.CPUUpdate, wantRequests: 1, wantCompleted: 0},
		{name: "memory", failed: contractmock.MemoryUpdate, wantRequests: 2, wantCompleted: 1},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := newServer(t, contractmock.Plan{Replies: map[string]contractmock.Reply{
				test.failed: {Status: http.StatusServiceUnavailable, Body: errorBody},
			}})
			runtime := server.Runtime()
			client := newClient(t, server)
			report, err := client.ResizeAndStart(
				context.Background(), runtime.VM, runtime.CPUCount, runtime.MemoryMiB,
			)
			var apiError *resize.APIError
			if !errors.As(err, &apiError) || apiError.OperationID != test.failed {
				t.Fatalf("error = %T %#v, want APIError for %s", err, err, test.failed)
			}
			if report.OverallState != "FAILED" ||
				report.CompletedStepCount != test.wantCompleted ||
				report.FailedOperationID != test.failed ||
				len(report.Steps) != test.wantRequests ||
				report.Steps[len(report.Steps)-1].State != "FAILED" {
				t.Fatalf("early-failure report mismatch: %#v", report)
			}
			if got := len(server.Requests()); got != test.wantRequests {
				t.Fatalf("sent %d requests after %s failure, want %d",
					got, test.failed, test.wantRequests)
			}
		})
	}
}

func TestMalformedVAPIErrorPreservesEarlierStepsWithoutInventingFailure(t *testing.T) {
	server := newServer(t, contractmock.Plan{Replies: map[string]contractmock.Reply{
		contractmock.PowerStart: {
			Status:  http.StatusServiceUnavailable,
			RawBody: []byte(`{"error_type":"SERVICE_UNAVAILABLE","messages":[]}`),
		},
	}})
	runtime := server.Runtime()
	client := newClient(t, server)
	report, err := client.ResizeAndStart(
		context.Background(), runtime.VM, runtime.CPUCount, runtime.MemoryMiB,
	)
	var protocolError *resize.ProtocolError
	if !errors.As(err, &protocolError) ||
		protocolError.OperationID != resize.PowerStartOperation {
		t.Fatalf("error = %T %#v, want power-start ProtocolError", err, err)
	}
	if report.CompletedStepCount != 2 ||
		report.FailedOperationID != resize.PowerStartOperation ||
		len(report.Steps) != 2 {
		t.Fatalf("malformed error fabricated or lost a step: %#v", report)
	}
}

func TestLocalValidationSendsNoTraffic(t *testing.T) {
	var calls atomic.Int32
	transport := roundTripFunc(func(*http.Request) (*http.Response, error) {
		calls.Add(1)
		return nil, errors.New("network must not be reached")
	})
	validConfig := resize.Config{
		BaseURL:      "http://127.0.0.1:39091",
		SessionToken: "local-validation-token",
		HTTPClient:   &http.Client{Transport: transport},
	}
	configTests := []struct {
		name   string
		mutate func(*resize.Config)
	}{
		{name: "relative_url", mutate: func(c *resize.Config) { c.BaseURL = "/relative" }},
		{name: "non_http_url", mutate: func(c *resize.Config) { c.BaseURL = "ftp://127.0.0.1" }},
		{name: "embedded_credentials", mutate: func(c *resize.Config) { c.BaseURL = "http://user@127.0.0.1" }},
		{name: "non_root_path", mutate: func(c *resize.Config) { c.BaseURL = "http://127.0.0.1/api" }},
		{name: "escaped_path", mutate: func(c *resize.Config) { c.BaseURL = "http://127.0.0.1/%2f" }},
		{name: "query", mutate: func(c *resize.Config) { c.BaseURL = "http://127.0.0.1?x=1" }},
		{name: "empty_query", mutate: func(c *resize.Config) { c.BaseURL = "http://127.0.0.1?" }},
		{name: "fragment", mutate: func(c *resize.Config) { c.BaseURL = "http://127.0.0.1#x" }},
		{name: "invalid_port", mutate: func(c *resize.Config) { c.BaseURL = "http://127.0.0.1:bad" }},
		{name: "empty_token", mutate: func(c *resize.Config) { c.SessionToken = "" }},
		{name: "blank_token", mutate: func(c *resize.Config) { c.SessionToken = "   " }},
		{name: "token_whitespace", mutate: func(c *resize.Config) { c.SessionToken = "bad token" }},
		{name: "token_newline", mutate: func(c *resize.Config) { c.SessionToken = "bad\nvalue" }},
		{name: "token_non_ascii", mutate: func(c *resize.Config) { c.SessionToken = "bad-✓" }},
	}
	for _, test := range configTests {
		t.Run("config_"+test.name, func(t *testing.T) {
			config := validConfig
			test.mutate(&config)
			if client, err := resize.NewClient(config); err == nil || client != nil {
				t.Fatalf("NewClient(%+v) = %+v, %v; want local error", config, client, err)
			}
		})
	}

	client, err := resize.NewClient(validConfig)
	if err != nil {
		t.Fatalf("NewClient(valid): %v", err)
	}
	inputTests := []struct {
		name      string
		ctx       context.Context
		vm        string
		cpu       int64
		memoryMiB int64
	}{
		{name: "nil_context", vm: "vm-1", cpu: 2, memoryMiB: 4096},
		{name: "empty_vm", ctx: context.Background(), vm: "", cpu: 2, memoryMiB: 4096},
		{name: "blank_vm", ctx: context.Background(), vm: " \t", cpu: 2, memoryMiB: 4096},
		{name: "zero_cpu", ctx: context.Background(), vm: "vm-1", cpu: 0, memoryMiB: 4096},
		{name: "negative_cpu", ctx: context.Background(), vm: "vm-1", cpu: -1, memoryMiB: 4096},
		{name: "zero_memory", ctx: context.Background(), vm: "vm-1", cpu: 2, memoryMiB: 0},
		{name: "negative_memory", ctx: context.Background(), vm: "vm-1", cpu: 2, memoryMiB: -1},
	}
	for _, test := range inputTests {
		t.Run("input_"+test.name, func(t *testing.T) {
			report, err := client.ResizeAndStart(
				test.ctx, test.vm, test.cpu, test.memoryMiB,
			)
			if err == nil || len(report.Steps) != 0 {
				t.Fatalf("invalid input returned report=%#v error=%v", report, err)
			}
		})
	}
	if got := calls.Load(); got != 0 {
		t.Fatalf("local validation sent %d HTTP requests", got)
	}
}

func TestTransportErrorIsRedacted(t *testing.T) {
	const leakedCause = "dial failed with sensitive transport detail"
	const token = "sensitive-session-token"
	client, err := resize.NewClient(resize.Config{
		BaseURL:      "http://127.0.0.1:39092",
		SessionToken: token,
		HTTPClient: &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
			return nil, errors.New(leakedCause)
		})},
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	report, err := client.ResizeAndStart(context.Background(), "vm-1", 2, 4096)
	var transportError *resize.TransportError
	if !errors.As(err, &transportError) ||
		transportError.OperationID != resize.CPUUpdateOperation {
		t.Fatalf("error = %T %#v, want CPU TransportError", err, err)
	}
	if len(report.Steps) != 0 ||
		report.FailedOperationID != resize.CPUUpdateOperation {
		t.Fatalf("transport report mismatch: %#v", report)
	}
	if strings.Contains(err.Error(), leakedCause) || strings.Contains(err.Error(), token) {
		t.Fatalf("transport error exposed sensitive data: %q", err.Error())
	}
}

func TestCanceledContextIsPreserved(t *testing.T) {
	server := newServer(t, contractmock.Plan{})
	client := newClient(t, server)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	runtime := server.Runtime()
	report, err := client.ResizeAndStart(ctx, runtime.VM, runtime.CPUCount, runtime.MemoryMiB)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %T %v, want context.Canceled", err, err)
	}
	if len(report.Steps) != 0 || len(server.Requests()) != 0 {
		t.Fatalf("canceled call changed state: report=%#v requests=%#v",
			report, server.Requests())
	}
}

func TestContractMockRejectsOperationsOutsideFocusedContract(t *testing.T) {
	server := newServer(t, contractmock.Plan{})
	request, err := http.NewRequest(
		http.MethodGet,
		server.URL()+"/api/vcenter/vm/outside/power",
		nil,
	)
	if err != nil {
		t.Fatal(err)
	}
	response, err := server.Client().Do(request)
	if err != nil {
		t.Fatalf("out-of-contract request: %v", err)
	}
	defer response.Body.Close()
	body, _ := io.ReadAll(response.Body)
	if response.StatusCode != http.StatusNotFound ||
		!strings.Contains(string(body), "NOT_FOUND") {
		t.Fatalf("out-of-contract response = %d %s", response.StatusCode, body)
	}
	requests := server.Requests()
	if len(requests) != 1 || requests[0].OperationID != "" {
		t.Fatalf("out-of-contract log entry = %#v", requests)
	}
}

func newServer(t *testing.T, plan contractmock.Plan) *contractmock.Server {
	t.Helper()
	server, err := contractmock.New("docs/contract.json", plan)
	if err != nil {
		t.Fatalf("start contract mock: %v", err)
	}
	t.Cleanup(server.Close)
	return server
}

func newClient(t *testing.T, server *contractmock.Server) *resize.Client {
	t.Helper()
	client, err := resize.NewClient(resize.Config{
		BaseURL:      server.URL(),
		SessionToken: server.Runtime().SessionToken,
		HTTPClient:   server.Client(),
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func assertOptionalProperties(
	t *testing.T,
	required []string,
	properties map[string]struct {
		Type          string `json:"type"`
		Format        string `json:"format"`
		Required      bool   `json:"required"`
		UnsetBehavior string `json:"unsetBehavior"`
	},
	wantNames []string,
) {
	t.Helper()
	if len(required) != 0 {
		t.Fatalf("update schema unexpectedly requires properties: %v", required)
	}
	gotNames := make([]string, 0, len(properties))
	for name, property := range properties {
		gotNames = append(gotNames, name)
		if property.Required || property.UnsetBehavior != "unchanged" {
			t.Fatalf("property %q lost optional/unset semantics: %+v", name, property)
		}
	}
	sortStrings(gotNames)
	if !reflect.DeepEqual(gotNames, wantNames) {
		t.Fatalf("schema properties = %v, want %v", gotNames, wantNames)
	}
}

func assertJSONMembers(t *testing.T, body []byte, want []string) {
	t.Helper()
	var object map[string]json.RawMessage
	if err := json.Unmarshal(body, &object); err != nil {
		t.Fatalf("request body is not a JSON object: %v; body=%q", err, body)
	}
	got := make([]string, 0, len(object))
	for name := range object {
		got = append(got, name)
	}
	sortStrings(got)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("JSON member set = %v, want %v; body=%s", got, want, body)
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

func assertFileHash(t *testing.T, path, want string) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read protected %s: %v", path, err)
	}
	sum := sha256.Sum256(data)
	got := hex.EncodeToString(sum[:])
	if got != want {
		t.Fatalf("protected fixture %s hash = %s, want %s", path, got, want)
	}
}

func sortStrings(values []string) {
	for index := 1; index < len(values); index++ {
		for inner := index; inner > 0 && values[inner] < values[inner-1]; inner-- {
			values[inner], values[inner-1] = values[inner-1], values[inner]
		}
	}
}

func integerString(value int64) string {
	encoded, _ := json.Marshal(value)
	return string(encoded)
}

func boolCount(value bool) int {
	if value {
		return 1
	}
	return 0
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}
