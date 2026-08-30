package cpuguard_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"

	guard "vcf91-0114"
	"vcf91-0114/internal/contractmock"
)

const (
	contractPath = "docs/contract.json"
	sourcePath   = "docs/official_sources.json"
	commitSHA    = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	specPath     = "specifications/vsphere/openapi/automation/vcenter.yaml"
	testVM       = "vm blue/50%?雪"
	encodedVM    = "vm%20blue%2F50%25%3F%E9%9B%AA"
	testToken    = "session-token-vcf91-0114"
)

func TestGuardedCPUUpdateTable(t *testing.T) {
	tests := []struct {
		name         string
		scenario     contractmock.Scenario
		errorKind    string
		wantRequests int
		wantEffects  int
	}{
		{
			name:         "powered off passes and mutates",
			scenario:     contractmock.Scenario{PowerBody: []byte(`{"state":"POWERED_OFF","clean_power_off":false}`)},
			wantRequests: 2,
			wantEffects:  1,
		},
		{
			name:         "powered on blocks",
			scenario:     contractmock.Scenario{PowerState: "POWERED_ON"},
			errorKind:    "precheck",
			wantRequests: 1,
		},
		{
			name:         "suspended blocks",
			scenario:     contractmock.Scenario{PowerState: "SUSPENDED"},
			errorKind:    "precheck",
			wantRequests: 1,
		},
		{
			name:         "missing state is malformed",
			scenario:     contractmock.Scenario{PowerBody: []byte(`{"clean_power_off":true}`)},
			errorKind:    "protocol",
			wantRequests: 1,
		},
		{
			name:         "unknown state is malformed",
			scenario:     contractmock.Scenario{PowerBody: []byte(`{"state":"OFF"}`)},
			errorKind:    "protocol",
			wantRequests: 1,
		},
		{
			name:         "wrong optional type is malformed",
			scenario:     contractmock.Scenario{PowerBody: []byte(`{"state":"POWERED_OFF","clean_power_off":"yes"}`)},
			errorKind:    "protocol",
			wantRequests: 1,
		},
		{
			name:         "present optional null is malformed",
			scenario:     contractmock.Scenario{PowerBody: []byte(`{"state":"POWERED_OFF","clean_power_off":null}`)},
			errorKind:    "protocol",
			wantRequests: 1,
		},
		{
			name:         "trailing JSON is malformed",
			scenario:     contractmock.Scenario{PowerBody: []byte(`{"state":"POWERED_OFF"} {}`)},
			errorKind:    "protocol",
			wantRequests: 1,
		},
		{
			name:         "precheck HTTP error blocks",
			scenario:     contractmock.Scenario{PowerStatus: http.StatusServiceUnavailable},
			errorKind:    "api-power",
			wantRequests: 1,
		},
		{
			name: "redirect is not followed",
			scenario: contractmock.Scenario{
				PowerStatus:      http.StatusFound,
				RedirectLocation: "/outside-focused-contract",
			},
			errorKind:    "api-power",
			wantRequests: 1,
		},
		{
			name:         "mutation HTTP error is reported",
			scenario:     contractmock.Scenario{CPUStatus: http.StatusServiceUnavailable},
			errorKind:    "api-cpu",
			wantRequests: 2,
			wantEffects:  0,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			logPath := t.TempDir() + "/requests.jsonl"
			server, err := contractmock.New(contractPath, logPath, test.scenario)
			if err != nil {
				t.Fatalf("start contract mock: %v", err)
			}
			defer func() {
				if err := server.Close(); err != nil {
					t.Errorf("close contract mock: %v", err)
				}
			}()

			client, err := guard.NewClient(guard.Config{
				BaseURL:      server.URL(),
				SessionToken: testToken,
			})
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			result, callErr := client.SetCPUCountIfPoweredOff(
				context.Background(),
				testVM,
				7,
			)
			assertErrorKind(t, callErr, test.errorKind)
			if callErr == nil {
				want := guard.CPUUpdateResult{
					VM:                 testVM,
					PreviousPowerState: "POWERED_OFF",
					CPUCount:           7,
					CompletedOperationIDs: []string{
						guard.PowerGetOperation,
						guard.CPUUpdateOperation,
					},
				}
				if !reflect.DeepEqual(result, want) {
					t.Fatalf("result mismatch:\n got: %#v\nwant: %#v", result, want)
				}
			}

			records, err := contractmock.ReadLog(server.LogPath())
			if err != nil {
				t.Fatalf("read fsynced request log: %v", err)
			}
			if len(records) != test.wantRequests {
				t.Fatalf("request count = %d, want %d: %#v", len(records), test.wantRequests, records)
			}
			if len(records) >= 1 {
				assertPowerRequest(t, records[0])
			}
			if len(records) == 2 {
				assertCPURequest(t, records[1])
			}
			if got := server.EffectCount(); got != test.wantEffects {
				t.Fatalf("mutation effects = %d, want %d", got, test.wantEffects)
			}
		})
	}
}

func TestValidationAndCancellationCauseNoTraffic(t *testing.T) {
	logPath := t.TempDir() + "/requests.jsonl"
	server, err := contractmock.New(contractPath, logPath, contractmock.Scenario{})
	if err != nil {
		t.Fatalf("start contract mock: %v", err)
	}
	defer server.Close()

	callerClient := &http.Client{}
	client, err := guard.NewClient(guard.Config{
		BaseURL:      server.URL(),
		SessionToken: testToken,
		HTTPClient:   callerClient,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if callerClient.CheckRedirect != nil {
		t.Fatal("NewClient mutated caller-owned HTTP client")
	}

	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	tests := []struct {
		name  string
		ctx   context.Context
		vm    string
		count int64
		cause error
	}{
		{name: "nil context", ctx: nil, vm: "vm-1", count: 1},
		{name: "blank VM", ctx: context.Background(), vm: " \t", count: 1},
		{name: "zero count", ctx: context.Background(), vm: "vm-1", count: 0},
		{name: "negative count", ctx: context.Background(), vm: "vm-1", count: -1},
		{name: "already cancelled", ctx: cancelled, vm: "vm-1", count: 1, cause: context.Canceled},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := client.SetCPUCountIfPoweredOff(test.ctx, test.vm, test.count)
			if err == nil {
				t.Fatal("expected local error")
			}
			if test.cause != nil && !errors.Is(err, test.cause) {
				t.Fatalf("error %v does not preserve %v", err, test.cause)
			}
		})
	}
	records, err := contractmock.ReadLog(logPath)
	if err != nil {
		t.Fatalf("read request log: %v", err)
	}
	if len(records) != 0 {
		t.Fatalf("local failures made %d requests", len(records))
	}
	if server.EffectCount() != 0 {
		t.Fatal("local failures changed mock state")
	}
}

func TestConfigurationValidationTable(t *testing.T) {
	tests := []guard.Config{
		{BaseURL: "", SessionToken: "token"},
		{BaseURL: "ftp://vc.example", SessionToken: "token"},
		{BaseURL: "https://user@vc.example", SessionToken: "token"},
		{BaseURL: "https://vc.example/sdk", SessionToken: "token"},
		{BaseURL: "https://vc.example?x=1", SessionToken: "token"},
		{BaseURL: "https://vc.example#fragment", SessionToken: "token"},
		{BaseURL: "https://vc.example", SessionToken: ""},
		{BaseURL: "https://vc.example", SessionToken: " \t"},
		{BaseURL: "https://vc.example", SessionToken: "token\r\ninjected"},
	}
	for index, config := range tests {
		t.Run(fmt.Sprintf("invalid-%02d", index), func(t *testing.T) {
			if _, err := guard.NewClient(config); err == nil {
				t.Fatalf("NewClient(%#v) unexpectedly succeeded", config)
			}
		})
	}
}

func TestErrorRedactionAndFields(t *testing.T) {
	secretMessage := "server-secret-message"
	body := []byte(
		`{"error_type":"SERVICE_UNAVAILABLE","messages":[{"id":"vcf.failure","default_message":"` +
			secretMessage + `"}]}`,
	)
	server, err := contractmock.New(
		contractPath,
		t.TempDir()+"/requests.jsonl",
		contractmock.Scenario{
			PowerStatus: http.StatusServiceUnavailable,
			PowerBody:   body,
		},
	)
	if err != nil {
		t.Fatalf("start contract mock: %v", err)
	}
	defer server.Close()

	client, err := guard.NewClient(guard.Config{
		BaseURL:      server.URL(),
		SessionToken: testToken,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	_, err = client.SetCPUCountIfPoweredOff(context.Background(), "vm-1", 2)
	var apiError *guard.APIError
	if !errors.As(err, &apiError) {
		t.Fatalf("error = %T, want *APIError", err)
	}
	if apiError.OperationID != guard.PowerGetOperation ||
		apiError.StatusCode != http.StatusServiceUnavailable ||
		apiError.ErrorType != "SERVICE_UNAVAILABLE" ||
		len(apiError.Messages) != 1 ||
		apiError.Messages[0].ID != "vcf.failure" ||
		apiError.Messages[0].DefaultMessage != secretMessage {
		t.Fatalf("APIError fields not preserved: %#v", apiError)
	}
	assertDoesNotContain(t, err.Error(), testToken, secretMessage, string(body))

	transportSecret := "dial-secret-" + testToken
	transportClient := &http.Client{
		Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
			return nil, errors.New(transportSecret)
		}),
	}
	client, err = guard.NewClient(guard.Config{
		BaseURL:      "http://127.0.0.1:1",
		SessionToken: testToken,
		HTTPClient:   transportClient,
	})
	if err != nil {
		t.Fatalf("NewClient with test transport: %v", err)
	}
	_, err = client.SetCPUCountIfPoweredOff(context.Background(), "vm-1", 2)
	var transportError *guard.TransportError
	if !errors.As(err, &transportError) {
		t.Fatalf("error = %T, want *TransportError", err)
	}
	assertDoesNotContain(t, err.Error(), testToken, transportSecret)
}

func TestInFlightCancellationIsDiscoverable(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	transportClient := &http.Client{
		Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
			cancel()
			<-request.Context().Done()
			return nil, request.Context().Err()
		}),
	}
	client, err := guard.NewClient(guard.Config{
		BaseURL:      "http://127.0.0.1:1",
		SessionToken: testToken,
		HTTPClient:   transportClient,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	_, err = client.SetCPUCountIfPoweredOff(ctx, "vm-1", 2)
	var transportError *guard.TransportError
	if !errors.As(err, &transportError) {
		t.Fatalf("error = %T, want *TransportError", err)
	}
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error %v does not preserve context cancellation", err)
	}
}

func TestPinnedSpecificationProjection(t *testing.T) {
	contractData, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var contract struct {
		Source struct {
			CommitSHA string `json:"commitSha"`
			SpecPath  string `json:"specPath"`
			License   string `json:"license"`
		} `json:"source"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
		} `json:"operations"`
		Schemas map[string]struct {
			Properties map[string]json.RawMessage `json:"properties"`
		} `json:"schemas"`
	}
	if err := json.Unmarshal(contractData, &contract); err != nil {
		t.Fatalf("decode contract: %v", err)
	}
	if contract.Source.CommitSHA != commitSHA ||
		contract.Source.SpecPath != specPath ||
		contract.Source.License != "Apache-2.0" {
		t.Fatalf("contract source pin changed: %#v", contract.Source)
	}
	wantOperations := []struct {
		ID     string
		Method string
		Path   string
	}{
		{guard.PowerGetOperation, "GET", "/api/vcenter/vm/{vm}/power"},
		{guard.CPUUpdateOperation, "PATCH", "/api/vcenter/vm/{vm}/hardware/cpu"},
	}
	if len(contract.Operations) != len(wantOperations) {
		t.Fatalf("contract operation count = %d", len(contract.Operations))
	}
	for index, want := range wantOperations {
		got := contract.Operations[index]
		if got.OperationID != want.ID || got.Method != want.Method || got.Path != want.Path {
			t.Fatalf("contract operation %d = %#v, want %#v", index, got, want)
		}
	}
	cpuProperties := contract.Schemas["Vcenter.Vm.Hardware.Cpu.UpdateSpec"].Properties
	wantProperties := []string{"count", "cores_per_socket", "hot_add_enabled", "hot_remove_enabled"}
	if len(cpuProperties) != len(wantProperties) {
		t.Fatalf("CPU property count = %d, want %d", len(cpuProperties), len(wantProperties))
	}
	for _, name := range wantProperties {
		if _, ok := cpuProperties[name]; !ok {
			t.Fatalf("CPU contract missing property %q", name)
		}
	}

	sourceData, err := os.ReadFile(sourcePath)
	if err != nil {
		t.Fatalf("read official sources: %v", err)
	}
	var sources struct {
		RepositoryCommitSHA string   `json:"repositoryCommitSha"`
		SpecPath            string   `json:"specPath"`
		OperationIDs        []string `json:"operationIds"`
		Operations          []struct {
			OperationID         string `json:"operationId"`
			RepositoryCommitSHA string `json:"repositoryCommitSha"`
			SpecPath            string `json:"specPath"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(sourceData, &sources); err != nil {
		t.Fatalf("decode official sources: %v", err)
	}
	wantIDs := []string{guard.PowerGetOperation, guard.CPUUpdateOperation}
	if sources.RepositoryCommitSHA != commitSHA ||
		sources.SpecPath != specPath ||
		!reflect.DeepEqual(sources.OperationIDs, wantIDs) ||
		len(sources.Operations) != len(wantIDs) {
		t.Fatalf("official source pin changed: %#v", sources)
	}
	for index, operation := range sources.Operations {
		if operation.OperationID != wantIDs[index] ||
			operation.RepositoryCommitSHA != commitSHA ||
			operation.SpecPath != specPath {
			t.Fatalf("official source operation %d is not fully pinned: %#v", index, operation)
		}
	}
}

func assertErrorKind(t *testing.T, err error, kind string) {
	t.Helper()
	if kind == "" {
		if err != nil {
			t.Fatalf("unexpected error: %T %v", err, err)
		}
		return
	}
	if err == nil {
		t.Fatalf("expected %s error", kind)
	}
	switch kind {
	case "precheck":
		var target *guard.PrecheckError
		if !errors.As(err, &target) {
			t.Fatalf("error = %T, want *PrecheckError", err)
		}
		if target.VM != testVM ||
			(target.ObservedState != "POWERED_ON" && target.ObservedState != "SUSPENDED") {
			t.Fatalf("PrecheckError fields = %#v", target)
		}
	case "protocol":
		var target *guard.ProtocolError
		if !errors.As(err, &target) || target.OperationID != guard.PowerGetOperation {
			t.Fatalf("error = %#v, want power *ProtocolError", err)
		}
	case "api-power", "api-cpu":
		var target *guard.APIError
		if !errors.As(err, &target) {
			t.Fatalf("error = %T, want *APIError", err)
		}
		wantOperation := guard.PowerGetOperation
		if kind == "api-cpu" {
			wantOperation = guard.CPUUpdateOperation
		}
		if target.OperationID != wantOperation {
			t.Fatalf("APIError operation = %q, want %q", target.OperationID, wantOperation)
		}
	default:
		t.Fatalf("unknown test error kind %q", kind)
	}
}

func assertPowerRequest(t *testing.T, record contractmock.Record) {
	t.Helper()
	if record.OperationID != guard.PowerGetOperation ||
		record.Method != http.MethodGet ||
		record.RequestURI != "/api/vcenter/vm/"+encodedVM+"/power" {
		t.Fatalf("power request line mismatch: %#v", record)
	}
	assertCommonHeaders(t, record)
	if record.ContentLength != 0 || record.Body != "" {
		t.Fatalf("power GET had a body: %#v", record)
	}
	if values := headerValues(record, "Content-Type"); len(values) != 0 {
		t.Fatalf("power GET Content-Type = %#v, want absent", values)
	}
}

func assertCPURequest(t *testing.T, record contractmock.Record) {
	t.Helper()
	if record.OperationID != guard.CPUUpdateOperation ||
		record.Method != http.MethodPatch ||
		record.RequestURI != "/api/vcenter/vm/"+encodedVM+"/hardware/cpu" {
		t.Fatalf("CPU request line mismatch: %#v", record)
	}
	assertCommonHeaders(t, record)
	if got := headerValues(record, "Content-Type"); !reflect.DeepEqual(got, []string{"application/json"}) {
		t.Fatalf("CPU Content-Type = %#v", got)
	}
	const wantBody = `{"count":7}`
	if record.ContentLength != int64(len(wantBody)) || record.Body != wantBody {
		t.Fatalf("CPU body mismatch: length=%d body=%q", record.ContentLength, record.Body)
	}
	var object map[string]json.RawMessage
	if err := json.Unmarshal([]byte(record.Body), &object); err != nil {
		t.Fatalf("CPU body is not JSON: %v", err)
	}
	if len(object) != 1 || string(object["count"]) != "7" {
		t.Fatalf("CPU body member set = %#v; unset optionals were not omitted", object)
	}
	for _, forbidden := range []string{
		"cores_per_socket",
		"hot_add_enabled",
		"hot_remove_enabled",
	} {
		if _, ok := object[forbidden]; ok {
			t.Fatalf("unset optional field %q was sent", forbidden)
		}
	}
}

func assertCommonHeaders(t *testing.T, record contractmock.Record) {
	t.Helper()
	if got := headerValues(record, "Accept"); !reflect.DeepEqual(got, []string{"application/json"}) {
		t.Fatalf("Accept = %#v", got)
	}
	if got := headerValues(record, "vmware-api-session-id"); !reflect.DeepEqual(got, []string{testToken}) {
		t.Fatalf("session header = %#v", got)
	}
	if got := headerValues(record, "Authorization"); len(got) != 0 {
		t.Fatalf("Authorization must be absent, got %#v", got)
	}
}

func headerValues(record contractmock.Record, name string) []string {
	return record.Headers[http.CanonicalHeaderKey(name)]
}

func assertDoesNotContain(t *testing.T, text string, secrets ...string) {
	t.Helper()
	for _, secret := range secrets {
		if secret != "" && strings.Contains(text, secret) {
			t.Fatalf("error text exposed secret %q: %q", secret, text)
		}
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}
