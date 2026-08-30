package verification_test

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"sync"
	"testing"

	"example.com/vcf-installer-gate/installer"
	"example.com/vcf-installer-gate/internal/mockvcf"
)

const expectedRequestBody = `{"sddcId":"sfo01-m01","vcenterSpec":{"vcenterHostname":"vc-m01.example.test","rootVcenterPassword":"StrongPassword!9"},"networkSpecs":[{"networkType":"MANAGEMENT","vlanId":1000}],"dnsSpec":{"subdomain":"example.test"}}`

func minimalSpec() installer.SddcSpec {
	return installer.SddcSpec{
		SddcID: "sfo01-m01",
		VcenterSpec: installer.SddcVcenterSpec{
			VcenterHostname:     "vc-m01.example.test",
			RootVcenterPassword: "StrongPassword!9",
		},
		NetworkSpecs: []installer.SddcNetworkSpec{{
			NetworkType: "MANAGEMENT",
			VLANID:      1000,
		}},
		DNSSpec: installer.DnsSpec{Subdomain: "example.test"},
	}
}

func TestPrecheckAndDeployTable(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name               string
		scenario           mockvcf.Scenario
		wantTaskID         string
		wantPrecheckError  bool
		wantExecution      string
		wantResult         string
		wantAPIErrorOp     string
		wantAPIErrorStatus int
		wantOtherError     bool
		wantRequests       int
		wantMutations      int
	}{
		{
			name:          "completed successful precheck opens gate",
			scenario:      mockvcf.Scenario{},
			wantTaskID:    "sfo01-m01",
			wantRequests:  2,
			wantMutations: 1,
		},
		{
			name:          "accepted completed successful precheck opens gate",
			scenario:      mockvcf.Scenario{ValidationStatus: http.StatusAccepted},
			wantTaskID:    "sfo01-m01",
			wantRequests:  2,
			wantMutations: 1,
		},
		{
			name: "failed result keeps mutation gate closed",
			scenario: mockvcf.Scenario{Validation: mockvcf.ValidationResponse{
				ID:              "validation-failed",
				Description:     "precheck failed",
				ExecutionStatus: "COMPLETED",
				ResultStatus:    "FAILED",
			}},
			wantPrecheckError: true,
			wantExecution:     "COMPLETED",
			wantResult:        "FAILED",
			wantRequests:      1,
			wantMutations:     0,
		},
		{
			name: "warning result keeps mutation gate closed",
			scenario: mockvcf.Scenario{Validation: mockvcf.ValidationResponse{
				ID:              "validation-warning",
				Description:     "precheck warning",
				ExecutionStatus: "COMPLETED",
				ResultStatus:    "WARNING",
			}},
			wantPrecheckError: true,
			wantExecution:     "COMPLETED",
			wantResult:        "WARNING",
			wantRequests:      1,
			wantMutations:     0,
		},
		{
			name: "accepted but in progress precheck keeps mutation gate closed",
			scenario: mockvcf.Scenario{
				ValidationStatus: http.StatusAccepted,
				Validation: mockvcf.ValidationResponse{
					ID:              "validation-running",
					Description:     "precheck running",
					ExecutionStatus: "IN_PROGRESS",
					ResultStatus:    "SUCCEEDED",
				},
			},
			wantPrecheckError: true,
			wantExecution:     "IN_PROGRESS",
			wantResult:        "SUCCEEDED",
			wantRequests:      1,
			wantMutations:     0,
		},
		{
			name: "completed unknown result keeps mutation gate closed",
			scenario: mockvcf.Scenario{Validation: mockvcf.ValidationResponse{
				ID:              "validation-unknown",
				Description:     "precheck result is unknown",
				ExecutionStatus: "COMPLETED",
				ResultStatus:    "UNKNOWN",
			}},
			wantPrecheckError: true,
			wantExecution:     "COMPLETED",
			wantResult:        "UNKNOWN",
			wantRequests:      1,
			wantMutations:     0,
		},
		{
			name: "cancelled validation keeps mutation gate closed",
			scenario: mockvcf.Scenario{Validation: mockvcf.ValidationResponse{
				ID:              "validation-cancelled",
				Description:     "precheck was cancelled",
				ExecutionStatus: "CANCELLED",
				ResultStatus:    "CANCELLED",
			}},
			wantPrecheckError: true,
			wantExecution:     "CANCELLED",
			wantResult:        "CANCELLED",
			wantRequests:      1,
			wantMutations:     0,
		},
		{
			name:               "precheck HTTP error keeps mutation gate closed",
			scenario:           mockvcf.Scenario{ValidationStatus: http.StatusInternalServerError},
			wantAPIErrorOp:     installer.OperationValidateSddcSpec,
			wantAPIErrorStatus: http.StatusInternalServerError,
			wantRequests:       1,
			wantMutations:      0,
		},
		{
			name:               "undocumented precheck success status keeps mutation gate closed",
			scenario:           mockvcf.Scenario{ValidationStatus: http.StatusCreated},
			wantAPIErrorOp:     installer.OperationValidateSddcSpec,
			wantAPIErrorStatus: http.StatusCreated,
			wantRequests:       1,
			wantMutations:      0,
		},
		{
			name:           "invalid precheck response keeps mutation gate closed",
			scenario:       mockvcf.Scenario{ValidationRawBody: []byte(`{"resultStatus":`)},
			wantOtherError: true,
			wantRequests:   1,
			wantMutations:  0,
		},
		{
			name:               "deployment rejection is attributed to mutating operation",
			scenario:           mockvcf.Scenario{DeployStatus: http.StatusBadRequest},
			wantAPIErrorOp:     installer.OperationDeploySddc,
			wantAPIErrorStatus: http.StatusBadRequest,
			wantRequests:       2,
			wantMutations:      1,
		},
		{
			name:               "undocumented deployment success status is rejected",
			scenario:           mockvcf.Scenario{DeployStatus: http.StatusOK},
			wantAPIErrorOp:     installer.OperationDeploySddc,
			wantAPIErrorStatus: http.StatusOK,
			wantRequests:       2,
			wantMutations:      1,
		},
		{
			name:           "invalid deployment response is reported",
			scenario:       mockvcf.Scenario{DeploymentRawBody: []byte(`{"status":`)},
			wantOtherError: true,
			wantRequests:   2,
			wantMutations:  1,
		},
	}

	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			server := mockvcf.New(test.scenario)
			t.Cleanup(server.Close)

			client, err := installer.NewClient(server.URL(), "fixture-token", server.Client())
			if err != nil {
				t.Fatalf("NewClient() error = %v", err)
			}
			task, err := client.PrecheckAndDeploy(context.Background(), minimalSpec())

			if test.wantPrecheckError {
				var precheckError *installer.PrecheckError
				if !errors.As(err, &precheckError) {
					t.Fatalf("error = %v, want *installer.PrecheckError", err)
				}
				if precheckError.ExecutionStatus != test.wantExecution || precheckError.ResultStatus != test.wantResult {
					t.Fatalf("PrecheckError = %#v, want executionStatus %q resultStatus %q", precheckError, test.wantExecution, test.wantResult)
				}
			} else if test.wantAPIErrorOp != "" {
				var apiError *installer.APIError
				if !errors.As(err, &apiError) {
					t.Fatalf("error = %v, want *installer.APIError", err)
				}
				if apiError.OperationID != test.wantAPIErrorOp || apiError.StatusCode != test.wantAPIErrorStatus {
					t.Fatalf("APIError = %#v, want operation %q status %d", apiError, test.wantAPIErrorOp, test.wantAPIErrorStatus)
				}
			} else if test.wantOtherError {
				if err == nil {
					t.Fatal("PrecheckAndDeploy() error = nil, want response decode error")
				}
			} else {
				if err != nil {
					t.Fatalf("PrecheckAndDeploy() error = %v", err)
				}
				if task == nil || task.ID != test.wantTaskID || task.Status != "IN_PROGRESS" || task.CreationTimestamp == "" {
					t.Fatalf("task = %#v, want decoded deployment task %q", task, test.wantTaskID)
				}
			}

			requests := server.Requests()
			if len(requests) != test.wantRequests {
				t.Fatalf("request count = %d, want %d; requests = %#v", len(requests), test.wantRequests, requests)
			}
			if mutations := server.Mutations(); mutations != test.wantMutations {
				t.Fatalf("mutation count = %d, want %d", mutations, test.wantMutations)
			}
			assertWireRequests(t, requests)
		})
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func TestCallerContextIsPropagatedToBothOperations(t *testing.T) {
	t.Parallel()
	type contextKey struct{}
	const marker = "caller-context-marker"

	server := mockvcf.New(mockvcf.Scenario{})
	t.Cleanup(server.Close)
	baseTransport := server.Client().Transport
	var calls int
	clientHTTP := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if request.Context().Value(contextKey{}) != marker {
			return nil, errors.New("caller context value was not propagated")
		}
		calls++
		return baseTransport.RoundTrip(request)
	})}
	client, err := installer.NewClient(server.URL(), "fixture-token", clientHTTP)
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}
	ctx := context.WithValue(context.Background(), contextKey{}, marker)
	task, err := client.PrecheckAndDeploy(ctx, minimalSpec())
	if err != nil {
		t.Fatalf("PrecheckAndDeploy() error = %v", err)
	}
	if task == nil || task.ID != "sfo01-m01" || calls != 2 || server.Mutations() != 1 {
		t.Fatalf("task = %#v, calls = %d; want decoded task and two context-aware requests", task, calls)
	}
}

func assertWireRequests(t *testing.T, requests []mockvcf.Request) {
	t.Helper()
	wantURIs := []string{"/v1/sddcs/validations", "/v1/sddcs"}
	for i, request := range requests {
		if request.Method != http.MethodPost {
			t.Errorf("request %d method = %q, want POST", i, request.Method)
		}
		if request.RequestURI != wantURIs[i] {
			t.Errorf("request %d URI = %q, want %q (no optional query parameter)", i, request.RequestURI, wantURIs[i])
		}
		if got := request.Header.Get("Content-Type"); got != "application/json" {
			t.Errorf("request %d Content-Type = %q, want application/json", i, got)
		}
		if got := request.Header.Get("Accept"); got != "application/json" {
			t.Errorf("request %d Accept = %q, want application/json", i, got)
		}
		if got := request.Header.Get("Authorization"); got != "Bearer fixture-token" {
			t.Errorf("request %d Authorization = %q, want bearer token", i, got)
		}
		if got := string(request.Body); got != expectedRequestBody {
			t.Errorf("request %d body mismatch\n got: %s\nwant: %s", i, got, expectedRequestBody)
		}
		assertOptionalFieldsOmitted(t, request.Body)
	}
}

func assertOptionalFieldsOmitted(t *testing.T, body []byte) {
	t.Helper()
	var document map[string]any
	if err := json.Unmarshal(body, &document); err != nil {
		t.Fatalf("request body is not JSON: %v", err)
	}
	wantRoot := []string{"sddcId", "vcenterSpec", "networkSpecs", "dnsSpec"}
	if got := sortedKeys(document); !reflect.DeepEqual(got, sorted(wantRoot)) {
		t.Errorf("root JSON fields = %v, want exactly %v", got, sorted(wantRoot))
	}
	dns, ok := document["dnsSpec"].(map[string]any)
	if !ok || !reflect.DeepEqual(sortedKeys(dns), []string{"subdomain"}) {
		t.Errorf("dnsSpec fields = %v, want only subdomain", sortedKeys(dns))
	}
	vcenter, ok := document["vcenterSpec"].(map[string]any)
	if !ok || !reflect.DeepEqual(sortedKeys(vcenter), []string{"rootVcenterPassword", "vcenterHostname"}) {
		t.Errorf("vcenterSpec contains unset optional fields: %v", sortedKeys(vcenter))
	}
	networks, ok := document["networkSpecs"].([]any)
	if !ok || len(networks) != 1 {
		t.Fatalf("networkSpecs = %#v, want one network", document["networkSpecs"])
	}
	network, ok := networks[0].(map[string]any)
	if !ok || !reflect.DeepEqual(sortedKeys(network), []string{"networkType", "vlanId"}) {
		t.Errorf("networkSpecs[0] contains unset optional fields: %v", sortedKeys(network))
	}
}

func sortedKeys(value map[string]any) []string {
	keys := make([]string, 0, len(value))
	for key := range value {
		keys = append(keys, key)
	}
	return sorted(keys)
}

func sorted(values []string) []string {
	result := append([]string(nil), values...)
	for i := 1; i < len(result); i++ {
		for j := i; j > 0 && result[j] < result[j-1]; j-- {
			result[j], result[j-1] = result[j-1], result[j]
		}
	}
	return result
}

func TestContractAndOfficialSourcesArePinned(t *testing.T) {
	t.Parallel()

	contractBytes := readProjectFile(t, "docs", "contract.json")
	var contract struct {
		Source struct {
			APIVersion    string `json:"api_version"`
			RepositorySHA string `json:"repository_commit_sha"`
			SpecPath      string `json:"spec_path"`
		} `json:"source"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(contractBytes, &contract); err != nil {
		t.Fatalf("decode contract.json: %v", err)
	}
	if contract.Source.APIVersion != "9.1.0.0" || contract.Source.RepositorySHA != "3949fc33339fc5ea1b77eadb258f1cf49aa88e26" || contract.Source.SpecPath != "specifications/vcf-installer/vcf-installer-openapi.json" {
		t.Fatalf("unexpected source pin: %#v", contract.Source)
	}
	wantOperations := []mockvcf.Operation{
		{OperationID: "validateSddcSpec", Method: "POST", Path: "/v1/sddcs/validations"},
		{OperationID: "deploySddc", Method: "POST", Path: "/v1/sddcs"},
	}
	gotOperations := make([]mockvcf.Operation, len(contract.Operations))
	for i, operation := range contract.Operations {
		gotOperations[i] = mockvcf.Operation(operation)
	}
	if !reflect.DeepEqual(gotOperations, wantOperations) {
		t.Fatalf("contract operations = %#v, want %#v", gotOperations, wantOperations)
	}
	if got := mockvcf.AllowedOperations(); !reflect.DeepEqual(got, wantOperations) {
		t.Fatalf("mock operations = %#v, want contract operations %#v", got, wantOperations)
	}

	var sources struct {
		RepositorySHA string   `json:"repository_commit_sha"`
		SpecPath      string   `json:"spec_path"`
		OperationIDs  []string `json:"operation_ids"`
	}
	if err := json.Unmarshal(readProjectFile(t, "docs", "official_sources.json"), &sources); err != nil {
		t.Fatalf("decode official_sources.json: %v", err)
	}
	if sources.RepositorySHA != contract.Source.RepositorySHA || sources.SpecPath != contract.Source.SpecPath || !reflect.DeepEqual(sources.OperationIDs, []string{"validateSddcSpec", "deploySddc"}) {
		t.Fatalf("official source provenance does not match contract: %#v", sources)
	}
}

func TestMockRejectsOperationsOutsideContract(t *testing.T) {
	t.Parallel()
	server := mockvcf.New(mockvcf.Scenario{})
	t.Cleanup(server.Close)

	for _, target := range []string{"/v1/sddcs/validations", "/v1/sddcs/latest", "/v1/tokens"} {
		response, err := server.Client().Get(server.URL() + target)
		if err != nil {
			t.Fatalf("GET %s: %v", target, err)
		}
		_ = response.Body.Close()
		if response.StatusCode != http.StatusNotFound {
			t.Errorf("GET %s status = %d, want 404", target, response.StatusCode)
		}
	}
	if server.Mutations() != 0 {
		t.Fatal("unnamed operations changed mock state")
	}
}

func TestCallerContextStopsWorkflowBeforeMutation(t *testing.T) {
	t.Parallel()
	server := mockvcf.New(mockvcf.Scenario{})
	t.Cleanup(server.Close)
	client, err := installer.NewClient(server.URL(), "fixture-token", server.Client())
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := client.PrecheckAndDeploy(ctx, minimalSpec()); !errors.Is(err, context.Canceled) {
		t.Fatalf("PrecheckAndDeploy() error = %v, want context.Canceled", err)
	}
	if requests := server.Requests(); len(requests) != 0 {
		t.Fatalf("canceled workflow made requests: %#v", requests)
	}
	if server.Mutations() != 0 {
		t.Fatal("canceled workflow changed mock state")
	}
}

func TestClientIsSafeForConcurrentUse(t *testing.T) {
	t.Parallel()
	server := mockvcf.New(mockvcf.Scenario{})
	t.Cleanup(server.Close)
	client, err := installer.NewClient(server.URL(), "fixture-token", server.Client())
	if err != nil {
		t.Fatalf("NewClient() error = %v", err)
	}

	const calls = 8
	errorsFromCalls := make(chan error, calls)
	var wait sync.WaitGroup
	for i := 0; i < calls; i++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			_, err := client.PrecheckAndDeploy(context.Background(), minimalSpec())
			errorsFromCalls <- err
		}()
	}
	wait.Wait()
	close(errorsFromCalls)
	for err := range errorsFromCalls {
		if err != nil {
			t.Errorf("concurrent PrecheckAndDeploy() error = %v", err)
		}
	}
	if requests := server.Requests(); len(requests) != calls*2 {
		t.Fatalf("concurrent request count = %d, want %d", len(requests), calls*2)
	}
	if mutations := server.Mutations(); mutations != calls {
		t.Fatalf("concurrent mutation count = %d, want %d", mutations, calls)
	}
}

func readProjectFile(t *testing.T, elements ...string) []byte {
	t.Helper()
	parts := append([]string{"..", ".."}, elements...)
	data, err := os.ReadFile(filepath.Join(parts...))
	if err != nil {
		t.Fatalf("read %s: %v", filepath.Join(elements...), err)
	}
	return data
}
