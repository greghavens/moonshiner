package verification_test

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	installer "example.com/vcf-installer-client"
	"example.com/vcf-installer-client/internal/contractmock"
)

const (
	contractPath = "../../docs/contract.json"
	sourcesPath  = "../../docs/official_sources.json"
)

func TestOfficialContractPin(t *testing.T) {
	contractBytes := mustRead(t, contractPath)
	sourcesBytes := mustRead(t, sourcesPath)

	wantOperations := []string{
		"validateSddcSpec",
		"getSddcSpecValidation",
		"refreshAccessToken",
		"deploySddc",
	}

	tests := []struct {
		name string
		got  string
		want string
	}{
		{name: "contract sha256", got: digest(contractBytes), want: "8d1d3e413caeb83392b8afbe7f02c94bd340e65a6282884dd2a811a4d47fb776"},
		{name: "sources sha256", got: digest(sourcesBytes), want: "ba20757decf48fd273fa766472bbd0f8ef033d9564c2fba6d67f01850452b9e3"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if test.got != test.want {
				t.Fatalf("got %s, want %s; protected spec-derived documentation changed", test.got, test.want)
			}
		})
	}

	var contract struct {
		APIVersion string `json:"apiVersion"`
		Source     struct {
			Tag       string `json:"tag"`
			CommitSHA string `json:"commitSha"`
			Path      string `json:"path"`
		} `json:"source"`
		Operations []struct {
			OperationID string `json:"operationId"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(contractBytes, &contract); err != nil {
		t.Fatal(err)
	}
	if contract.APIVersion != "9.0.0.0" || contract.Source.Tag != "9.0.0.0" ||
		contract.Source.CommitSHA != "85151f6b1bb58f13b6ac0304bfec53904bea085f" ||
		contract.Source.Path != "specifications/vcf-installer/vcf-installer-openapi.json" {
		t.Fatalf("contract has the wrong official source pin: %+v", contract.Source)
	}
	gotOperations := make([]string, 0, len(contract.Operations))
	for _, operation := range contract.Operations {
		gotOperations = append(gotOperations, operation.OperationID)
	}
	if !reflect.DeepEqual(gotOperations, wantOperations) {
		t.Fatalf("operationIds = %v, want %v", gotOperations, wantOperations)
	}

	var sources struct {
		Sources []struct {
			Tag          string   `json:"tag"`
			CommitSHA    string   `json:"commit_sha"`
			SpecPath     string   `json:"spec_path"`
			OperationIDs []string `json:"operation_ids"`
		} `json:"sources"`
	}
	if err := json.Unmarshal(sourcesBytes, &sources); err != nil {
		t.Fatal(err)
	}
	if len(sources.Sources) != 1 {
		t.Fatalf("official source count = %d, want 1", len(sources.Sources))
	}
	source := sources.Sources[0]
	if source.Tag != "9.0.0.0" || source.CommitSHA != "85151f6b1bb58f13b6ac0304bfec53904bea085f" ||
		source.SpecPath != "specifications/vcf-installer/vcf-installer-openapi.json" ||
		!reflect.DeepEqual(source.OperationIDs, wantOperations) {
		t.Fatalf("unexpected official source record: %+v", source)
	}
}

func TestContractMockServesOnlyNamedOperations(t *testing.T) {
	contractBytes := mustRead(t, contractPath)

	tests := []struct {
		name   string
		method string
		path   string
	}{
		{name: "unselected installer operation", method: http.MethodGet, path: "/v1/sddcs"},
		{name: "unselected token operation", method: http.MethodPost, path: "/v1/tokens"},
		{name: "wrong method for validation", method: http.MethodPut, path: "/v1/sddcs/validations"},
		{name: "unknown route", method: http.MethodGet, path: "/health"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server, err := contractmock.New(contractBytes)
			if err != nil {
				t.Fatal(err)
			}
			defer server.Close()

			request, err := http.NewRequest(test.method, server.URL()+test.path, nil)
			if err != nil {
				t.Fatal(err)
			}
			response, err := server.Client().Do(request)
			if err != nil {
				t.Fatal(err)
			}
			response.Body.Close()
			if response.StatusCode != http.StatusNotFound {
				t.Fatalf("status = %d, want 404", response.StatusCode)
			}
			requests := server.Requests()
			if len(requests) != 1 || requests[0].Method != test.method || requests[0].Path != test.path {
				t.Fatalf("request log = %+v", requests)
			}
		})
	}
}

func TestValidateAndDeployRefreshesWithoutLosingWork(t *testing.T) {
	contractBytes := mustRead(t, contractPath)
	server, err := contractmock.New(contractBytes)
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()

	client, err := installer.NewClient(server.URL(), "access-old", "refresh-fixture", server.Client())
	if err != nil {
		t.Fatal(err)
	}
	spec := fixtureSpec()
	task, err := client.ValidateAndDeploy(context.Background(), spec, installer.DeployOptions{}, time.Millisecond)
	if err != nil {
		t.Fatal(err)
	}
	if task.ID != contractmock.TaskID || task.Status != "IN_PROGRESS" || task.CreationTimestamp != "2025-06-17T12:00:00Z" {
		t.Fatalf("unexpected task: %+v", task)
	}

	requests := server.Requests()
	want := []struct {
		method        string
		path          string
		rawQuery      string
		authorization string
		contentType   string
	}{
		{method: "POST", path: "/v1/sddcs/validations", authorization: "Bearer access-old", contentType: "application/json"},
		{method: "GET", path: "/v1/sddcs/validations/" + contractmock.ValidationID, authorization: "Bearer access-old"},
		{method: "PATCH", path: "/v1/tokens/access-token/refresh", contentType: "application/json"},
		{method: "GET", path: "/v1/sddcs/validations/" + contractmock.ValidationID, authorization: "Bearer access-new"},
		{method: "GET", path: "/v1/sddcs/validations/" + contractmock.ValidationID, authorization: "Bearer access-new"},
		{method: "POST", path: "/v1/sddcs", authorization: "Bearer access-new", contentType: "application/json"},
	}
	if len(requests) != len(want) {
		t.Fatalf("request count = %d, want %d\nrequests: %+v", len(requests), len(want), requests)
	}
	for i, expected := range want {
		t.Run(expected.method+" "+expected.path, func(t *testing.T) {
			got := requests[i]
			if got.Method != expected.method || got.Path != expected.path || got.RawQuery != expected.rawQuery ||
				got.Authorization != expected.authorization || got.ContentType != expected.contentType || got.Accept != "application/json" {
				t.Fatalf("request[%d] = %+v, want method=%s path=%s query=%q auth=%q content-type=%q accept=application/json",
					i, got, expected.method, expected.path, expected.rawQuery, expected.authorization, expected.contentType)
			}
		})
	}

	wantBody := map[string]any{
		"sddcId": "sfo01-m01",
		"vcenterSpec": map[string]any{
			"vcenterHostname":     "vcenter.rainpole.io",
			"rootVcenterPassword": "Sample_Password123",
		},
		"networkSpecs": []any{map[string]any{"networkType": "MANAGEMENT", "vlanId": float64(1000)}},
		"dnsSpec":      map[string]any{"subdomain": "rainpole.io"},
	}
	assertExactJSON(t, requests[0].Body, wantBody)
	assertExactJSON(t, requests[2].Body, "refresh-fixture")
	assertExactJSON(t, requests[5].Body, wantBody)
	if len(requests[1].Body) != 0 || len(requests[3].Body) != 0 || len(requests[4].Body) != 0 {
		t.Fatalf("GET requests must have empty bodies: first=%q retry=%q final=%q", requests[1].Body, requests[3].Body, requests[4].Body)
	}
	if !bytes.Equal(requests[0].Body, requests[5].Body) {
		t.Fatalf("deployment body differs from the validated body\nvalidate: %s\ndeploy:   %s", requests[0].Body, requests[5].Body)
	}

	var sent map[string]any
	if err := json.Unmarshal(requests[0].Body, &sent); err != nil {
		t.Fatal(err)
	}
	omitted := []string{"workflowType", "version", "ntpServers", "ceipEnabled", "skipEsxThumbprintValidation", "skipGatewayPingValidation"}
	for _, key := range omitted {
		t.Run("omit "+key, func(t *testing.T) {
			if _, exists := sent[key]; exists {
				t.Fatalf("unset optional field %q was sent: %s", key, requests[0].Body)
			}
		})
	}
}

func TestSkipValidationsQueryPreservesExplicitValues(t *testing.T) {
	contractBytes := mustRead(t, contractPath)
	for _, value := range []bool{false, true} {
		t.Run(fmt.Sprintf("%t", value), func(t *testing.T) {
			server, err := contractmock.New(contractBytes)
			if err != nil {
				t.Fatal(err)
			}
			defer server.Close()

			client, err := installer.NewClient(server.URL(), "access-old", "refresh-fixture", server.Client())
			if err != nil {
				t.Fatal(err)
			}
			if _, err := client.ValidateAndDeploy(context.Background(), fixtureSpec(), installer.DeployOptions{SkipValidations: &value}, 0); err != nil {
				t.Fatal(err)
			}
			requests := server.Requests()
			if got, want := requests[len(requests)-1].RawQuery, fmt.Sprintf("skipValidations=%t", value); got != want {
				t.Fatalf("deployment query = %q, want %q", got, want)
			}
		})
	}
}

func TestValidateAndDeployReportsServiceAndValidationFailures(t *testing.T) {
	contractBytes := mustRead(t, contractPath)
	tests := []struct {
		name      string
		scenario  contractmock.Scenario
		fragments []string
		requests  int
	}{
		{name: "validation request", scenario: contractmock.ValidationRejected, fragments: []string{"validateSddcSpec", "HTTP 422", "specification was rejected"}, requests: 1},
		{name: "validation lookup", scenario: contractmock.ValidationPollRejected, fragments: []string{"getSddcSpecValidation", "HTTP 503", "validation lookup unavailable"}, requests: 2},
		{name: "token refresh", scenario: contractmock.RefreshRejected, fragments: []string{"refreshAccessToken", "HTTP 403", "refresh token rejected"}, requests: 3},
		{name: "unsuccessful validation", scenario: contractmock.ValidationUnsuccessful, fragments: []string{"execution status \"COMPLETED\"", "result status \"FAILED\""}, requests: 4},
		{name: "deployment", scenario: contractmock.DeploymentRejected, fragments: []string{"deploySddc", "HTTP 409", "deployment conflict"}, requests: 6},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server, err := contractmock.NewForScenario(contractBytes, test.scenario)
			if err != nil {
				t.Fatal(err)
			}
			defer server.Close()

			client, err := installer.NewClient(server.URL(), "access-old", "refresh-fixture", server.Client())
			if err != nil {
				t.Fatal(err)
			}
			_, err = client.ValidateAndDeploy(context.Background(), fixtureSpec(), installer.DeployOptions{}, 0)
			if err == nil {
				t.Fatal("ValidateAndDeploy succeeded, want an error")
			}
			for _, fragment := range test.fragments {
				if !strings.Contains(err.Error(), fragment) {
					t.Fatalf("error %q does not contain %q", err, fragment)
				}
			}
			requests := server.Requests()
			if len(requests) != test.requests {
				t.Fatalf("request count = %d, want %d: %+v", len(requests), test.requests, requests)
			}
			validationSubmissions, deployments := 0, 0
			for _, request := range requests {
				if request.Method == http.MethodPost && request.Path == "/v1/sddcs/validations" {
					validationSubmissions++
				}
				if request.Method == http.MethodPost && request.Path == "/v1/sddcs" {
					deployments++
				}
			}
			if validationSubmissions != 1 {
				t.Fatalf("validation submissions = %d, want 1", validationSubmissions)
			}
			if test.scenario != contractmock.DeploymentRejected && deployments != 0 {
				t.Fatalf("deployments = %d after a pre-deployment failure, want 0", deployments)
			}
		})
	}
}

type contextMarker struct{}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}

func TestCallerContextIsPropagatedAndStopsPolling(t *testing.T) {
	ctx, cancel := context.WithCancel(context.WithValue(context.Background(), contextMarker{}, "caller"))
	defer cancel()
	calls := 0
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		calls++
		if request.Context().Value(contextMarker{}) != "caller" {
			return nil, errors.New("caller context value was not propagated")
		}
		if calls != 1 || request.Method != http.MethodPost || request.URL.Path != "/v1/sddcs/validations" {
			return nil, fmt.Errorf("unexpected request %d: %s %s", calls, request.Method, request.URL.Path)
		}
		cancel()
		return jsonHTTPResponse(http.StatusAccepted, `{"id":"validation","description":"fixture","executionStatus":"IN_PROGRESS","resultStatus":"UNKNOWN"}`), nil
	})
	client, err := installer.NewClient("http://127.0.0.1", "access-old", "refresh-fixture", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	_, err = client.ValidateAndDeploy(ctx, fixtureSpec(), installer.DeployOptions{}, time.Hour)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %v, want context.Canceled", err)
	}
	if calls != 1 {
		t.Fatalf("request count = %d after cancellation, want 1", calls)
	}
}

func TestPollIntervalDelaysValidationReads(t *testing.T) {
	const interval = 200 * time.Millisecond
	var validationReturned, validationPolled time.Time
	calls := 0
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		calls++
		switch calls {
		case 1:
			if request.Method != http.MethodPost || request.URL.Path != "/v1/sddcs/validations" {
				return nil, fmt.Errorf("request 1 = %s %s", request.Method, request.URL.Path)
			}
			validationReturned = time.Now()
			return jsonHTTPResponse(http.StatusAccepted, `{"id":"validation","description":"fixture","executionStatus":"IN_PROGRESS","resultStatus":"UNKNOWN"}`), nil
		case 2:
			if request.Method != http.MethodGet || request.URL.Path != "/v1/sddcs/validations/validation" {
				return nil, fmt.Errorf("request 2 = %s %s", request.Method, request.URL.Path)
			}
			validationPolled = time.Now()
			return jsonHTTPResponse(http.StatusOK, `{"id":"validation","description":"fixture","executionStatus":"COMPLETED","resultStatus":"SUCCEEDED"}`), nil
		case 3:
			if request.Method != http.MethodPost || request.URL.Path != "/v1/sddcs" {
				return nil, fmt.Errorf("request 3 = %s %s", request.Method, request.URL.Path)
			}
			return jsonHTTPResponse(http.StatusAccepted, `{"id":"task","name":"installation","status":"IN_PROGRESS","creationTimestamp":"2025-06-17T12:00:00Z"}`), nil
		default:
			return nil, fmt.Errorf("unexpected request %d: %s %s", calls, request.Method, request.URL.Path)
		}
	})
	client, err := installer.NewClient("http://127.0.0.1", "access-old", "refresh-fixture", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.ValidateAndDeploy(context.Background(), fixtureSpec(), installer.DeployOptions{}, interval); err != nil {
		t.Fatal(err)
	}
	if calls != 3 {
		t.Fatalf("request count = %d, want 3", calls)
	}
	if elapsed := validationPolled.Sub(validationReturned); elapsed < interval {
		t.Fatalf("validation was polled after %s, before poll interval %s", elapsed, interval)
	}
}

func TestClientSupportsConcurrentInstallations(t *testing.T) {
	contractBytes := mustRead(t, contractPath)
	server, err := contractmock.NewForScenario(contractBytes, contractmock.ConcurrentRefresh)
	if err != nil {
		t.Fatal(err)
	}
	defer server.Close()
	client, err := installer.NewClient(server.URL(), "access-old", "refresh-fixture", server.Client())
	if err != nil {
		t.Fatal(err)
	}

	const workers = 24
	start := make(chan struct{})
	errorsByWorker := make([]error, workers)
	var group sync.WaitGroup
	for i := range errorsByWorker {
		group.Add(1)
		go func(index int) {
			defer group.Done()
			<-start
			_, errorsByWorker[index] = client.ValidateAndDeploy(context.Background(), fixtureSpec(), installer.DeployOptions{}, 0)
		}(i)
	}
	close(start)
	group.Wait()
	for i, err := range errorsByWorker {
		if err != nil {
			t.Fatalf("worker %d: %v", i, err)
		}
	}
	validations, refreshes, deployments := 0, 0, 0
	for _, request := range server.Requests() {
		switch {
		case request.Method == http.MethodPost && request.Path == "/v1/sddcs/validations":
			validations++
		case request.Method == http.MethodPatch && request.Path == "/v1/tokens/access-token/refresh":
			refreshes++
		case request.Method == http.MethodPost && request.Path == "/v1/sddcs":
			deployments++
		}
	}
	if validations != workers || deployments != workers || refreshes < 1 {
		t.Fatalf("validations=%d refreshes=%d deployments=%d, want %d, at least 1, %d", validations, refreshes, deployments, workers, workers)
	}
}

func jsonHTTPResponse(status int, body string) *http.Response {
	return &http.Response{
		StatusCode: status,
		Header:     make(http.Header),
		Body:       io.NopCloser(strings.NewReader(body)),
	}
}

func TestExplicitFalseOptionIsNotTreatedAsUnset(t *testing.T) {
	falseValue := false
	tests := []struct {
		name string
		spec installer.SddcSpec
		key  string
	}{
		{name: "ceip", spec: withCEIP(fixtureSpec(), &falseValue), key: "ceipEnabled"},
		{name: "thumbprint validation", spec: withThumbprintValidation(fixtureSpec(), &falseValue), key: "skipEsxThumbprintValidation"},
		{name: "gateway ping validation", spec: withGatewayValidation(fixtureSpec(), &falseValue), key: "skipGatewayPingValidation"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			encoded, err := json.Marshal(test.spec)
			if err != nil {
				t.Fatal(err)
			}
			var object map[string]any
			if err := json.Unmarshal(encoded, &object); err != nil {
				t.Fatal(err)
			}
			value, exists := object[test.key]
			if !exists || value != false {
				t.Fatalf("%s = %#v, exists=%v; explicit false must be present", test.key, value, exists)
			}
		})
	}
}

func fixtureSpec() installer.SddcSpec {
	return installer.SddcSpec{
		SddcID: "sfo01-m01",
		VcenterSpec: installer.SddcVcenterSpec{
			VcenterHostname:     "vcenter.rainpole.io",
			RootVcenterPassword: "Sample_Password123",
		},
		NetworkSpecs: []installer.SddcNetworkSpec{{NetworkType: "MANAGEMENT", VlanID: 1000}},
		DNSSpec:      installer.DnsSpec{Subdomain: "rainpole.io"},
	}
}

func withCEIP(spec installer.SddcSpec, value *bool) installer.SddcSpec {
	spec.CeipEnabled = value
	return spec
}

func withThumbprintValidation(spec installer.SddcSpec, value *bool) installer.SddcSpec {
	spec.SkipEsxThumbprintValidation = value
	return spec
}

func withGatewayValidation(spec installer.SddcSpec, value *bool) installer.SddcSpec {
	spec.SkipGatewayPingValidation = value
	return spec
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	contents, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return contents
}

func digest(contents []byte) string {
	sum := sha256.Sum256(contents)
	return hex.EncodeToString(sum[:])
}

func assertExactJSON(t *testing.T, body []byte, want any) {
	t.Helper()
	decoder := json.NewDecoder(bytes.NewReader(body))
	var got any
	if err := decoder.Decode(&got); err != nil {
		t.Fatalf("invalid JSON body %q: %v", body, err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		t.Fatalf("trailing data in JSON body %q", body)
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("JSON body = %#v, want %#v", got, want)
	}
}
