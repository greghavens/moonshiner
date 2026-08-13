package verification_test

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"sort"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"example.com/vcf-installer-bootstrap/internal/contractmock"
	"example.com/vcf-installer-bootstrap/vcfinstaller"
)

const (
	wantCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	wantSpec   = "specifications/vcf-installer/vcf-installer-openapi.json"
)

func repositoryRoot(t *testing.T) string {
	t.Helper()
	_, filename, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("locate protected verifier")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(filename), "..", ".."))
}

func contractPath(t *testing.T) string {
	t.Helper()
	return filepath.Join(repositoryRoot(t), "docs", "contract.json")
}

func readJSON(t *testing.T, name string, destination any) {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(repositoryRoot(t), name))
	if err != nil {
		t.Fatalf("read %s: %v", name, err)
	}
	if err := json.Unmarshal(data, destination); err != nil {
		t.Fatalf("decode %s: %v", name, err)
	}
}

func TestOfficialSourceRecordsEveryContractOperation(t *testing.T) {
	type source struct {
		RepositoryCommitSHA string `json:"repositoryCommitSha"`
		SpecPath            string `json:"specPath"`
		OpenAPI             string `json:"openapi"`
		InfoVersion         string `json:"infoVersion"`
	}
	type response struct {
		Description string  `json:"description"`
		SchemaRef   *string `json:"schemaRef"`
	}
	type operation struct {
		OperationID string              `json:"operationId"`
		Method      string              `json:"method"`
		Path        string              `json:"path"`
		RequestBody json.RawMessage     `json:"requestBody"`
		Responses   map[string]response `json:"responses"`
	}
	var contract struct {
		Source     source      `json:"source"`
		Operations []operation `json:"operations"`
	}
	var official struct {
		RepositoryCommitSHA string   `json:"repositoryCommitSha"`
		SpecPath            string   `json:"specPath"`
		OperationIDs        []string `json:"operationIds"`
		Operations          []struct {
			OperationID         string `json:"operationId"`
			Method              string `json:"method"`
			Path                string `json:"path"`
			RepositoryCommitSHA string `json:"repositoryCommitSha"`
			SpecPath            string `json:"specPath"`
			SourcePointer       string `json:"sourcePointer"`
		} `json:"operations"`
	}
	readJSON(t, "docs/contract.json", &contract)
	readJSON(t, "docs/official_sources.json", &official)

	if contract.Source.RepositoryCommitSHA != wantCommit || contract.Source.SpecPath != wantSpec || contract.Source.OpenAPI != "3.0.1" || contract.Source.InfoVersion != "9.1.0.0" {
		t.Fatalf("contract source = %+v", contract.Source)
	}
	if official.RepositoryCommitSHA != wantCommit || official.SpecPath != wantSpec {
		t.Fatalf("official source = %s %s", official.RepositoryCommitSHA, official.SpecPath)
	}
	want := []struct {
		id      string
		method  string
		path    string
		pointer string
	}{
		{"updateProxyConfiguration", http.MethodPatch, "/v1/system/proxy-configuration", "/paths/~1v1~1system~1proxy-configuration/patch"},
		{"updateDepotSettings", http.MethodPut, "/v1/system/settings/depot", "/paths/~1v1~1system~1settings~1depot/put"},
		{"syncDepotMetadata", http.MethodPatch, "/v1/system/settings/depot/depot-sync-info", "/paths/~1v1~1system~1settings~1depot~1depot-sync-info/patch"},
	}
	if len(contract.Operations) != len(want) || len(official.Operations) != len(want) || len(official.OperationIDs) != len(want) {
		t.Fatalf("operation counts contract=%d official=%d ids=%d", len(contract.Operations), len(official.Operations), len(official.OperationIDs))
	}
	for index, expected := range want {
		got := contract.Operations[index]
		if got.OperationID != expected.id || got.Method != expected.method || got.Path != expected.path {
			t.Fatalf("contract operation %d = %+v", index, got)
		}
		if len(got.Responses) != 3 || got.Responses["202"].Description != "Accepted" {
			t.Fatalf("contract responses for %s = %+v", got.OperationID, got.Responses)
		}
		if expected.id == "syncDepotMetadata" {
			if string(got.RequestBody) != "null" {
				t.Fatalf("sync requestBody = %s, want null projection", got.RequestBody)
			}
		} else if string(got.RequestBody) == "null" || len(got.RequestBody) == 0 {
			t.Fatalf("%s request body projection is missing", expected.id)
		}
		recorded := official.Operations[index]
		if official.OperationIDs[index] != expected.id || recorded.OperationID != expected.id || recorded.Method != expected.method || recorded.Path != expected.path || recorded.RepositoryCommitSHA != wantCommit || recorded.SpecPath != wantSpec || recorded.SourcePointer != expected.pointer {
			t.Fatalf("official operation %d = %+v ids=%v", index, recorded, official.OperationIDs)
		}
	}
}

func pointer[T any](value T) *T { return &value }

func bootstrapInputs() (vcfinstaller.ProxyConfiguration, vcfinstaller.DepotSettings) {
	return vcfinstaller.ProxyConfiguration{
			IsEnabled:        pointer(true),
			Host:             pointer("proxy.bootstrap.example"),
			Port:             pointer[int32](8443),
			TransferProtocol: pointer("HTTPS"),
		}, vcfinstaller.DepotSettings{
			VMwareAccount: &vcfinstaller.DepotAccount{
				DownloadActivationCode: pointer("activation-0213"),
			},
			DepotConfiguration: &vcfinstaller.DepotConfiguration{IsOfflineDepot: false},
		}
}

func TestLaterSyncFailurePreservesAcceptedStepsAndExactWire(t *testing.T) {
	server := contractmock.Start(t, contractPath(t), contractmock.Scenario{
		FailOperation: "syncDepotMetadata",
		FailStatus:    http.StatusInternalServerError,
	})
	token := "runtime-token-0213"
	client, err := vcfinstaller.NewClient(server.URL(), token, &http.Client{Timeout: 2 * time.Second})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	proxy, depot := bootstrapInputs()
	report, err := client.ConfigureDepotAccess(context.Background(), proxy, depot)

	wantReport := vcfinstaller.ChangeReport{
		Outcome: vcfinstaller.OutcomePartialFailure,
		Steps: []vcfinstaller.StepResult{
			{OperationID: "updateProxyConfiguration", Status: vcfinstaller.StepAccepted, HTTPStatus: 202, TaskID: "task-proxy-0213"},
			{OperationID: "updateDepotSettings", Status: vcfinstaller.StepAccepted, HTTPStatus: 202},
			{OperationID: "syncDepotMetadata", Status: vcfinstaller.StepFailed, HTTPStatus: 500, ErrorCode: "VCF_DEPOT_SYNC_FAILED", ErrorMessage: "Depot metadata index could not be refreshed"},
		},
	}
	if !reflect.DeepEqual(report, wantReport) {
		t.Fatalf("report = %#v, want %#v", report, wantReport)
	}
	var apiError *vcfinstaller.APIError
	if !errors.As(err, &apiError) {
		t.Fatalf("error = %T %v, want *APIError", err, err)
	}
	wantAPI := &vcfinstaller.APIError{
		OperationID:        "syncDepotMetadata",
		StatusCode:         500,
		ErrorCode:          "VCF_DEPOT_SYNC_FAILED",
		ErrorType:          "INTERNAL_SERVER_ERROR",
		Message:            "Depot metadata index could not be refreshed",
		RemediationMessage: "Retry after depot connectivity is restored.",
		ReferenceToken:     "ref-sync-0213",
	}
	if !reflect.DeepEqual(apiError, wantAPI) {
		t.Fatalf("API error = %#v, want %#v", apiError, wantAPI)
	}

	requests := server.Requests()
	if len(requests) != 3 {
		t.Fatalf("request log = %v, want exactly three calls", requests)
	}
	wantBodies := [][]byte{
		[]byte(`{"isEnabled":true,"host":"proxy.bootstrap.example","port":8443,"transferProtocol":"HTTPS"}`),
		[]byte(`{"vmwareAccount":{"downloadActivationCode":"activation-0213"},"depotConfiguration":{"isOfflineDepot":false}}`),
		nil,
	}
	wantMethods := []string{http.MethodPatch, http.MethodPut, http.MethodPatch}
	wantTargets := []string{
		"/v1/system/proxy-configuration",
		"/v1/system/settings/depot",
		"/v1/system/settings/depot/depot-sync-info",
	}
	wantIDs := []string{"updateProxyConfiguration", "updateDepotSettings", "syncDepotMetadata"}
	for index, request := range requests {
		if request.OperationID != wantIDs[index] || request.Method != wantMethods[index] || request.RawTarget != wantTargets[index] {
			t.Fatalf("request %d = %+v", index+1, request)
		}
		if !reflect.DeepEqual(request.Body, wantBodies[index]) {
			t.Fatalf("request %d body = %q, want %q", index+1, request.Body, wantBodies[index])
		}
		assertSingleHeader(t, request.Header, "Authorization", "Bearer "+token)
		assertSingleHeader(t, request.Header, "Accept", "application/json")
		if index < 2 {
			assertSingleHeader(t, request.Header, "Content-Type", "application/json")
			if request.ContentLength != int64(len(wantBodies[index])) || len(request.TransferEncoding) != 0 {
				t.Errorf("request %d framing contentLength=%d transferEncoding=%v", index+1, request.ContentLength, request.TransferEncoding)
			}
			assertHeaderNames(t, request.Header, "Accept", "Accept-Encoding", "Authorization", "Content-Length", "Content-Type", "User-Agent")
		} else {
			if values := request.Header.Values("Content-Type"); len(values) != 0 {
				t.Errorf("sync Content-Type values = %v, want absent", values)
			}
			if len(request.Body) != 0 || request.ContentLength != 0 || len(request.TransferEncoding) != 0 {
				t.Errorf("sync framing body=%d contentLength=%d transferEncoding=%v", len(request.Body), request.ContentLength, request.TransferEncoding)
			}
			assertHeaderNames(t, request.Header, "Accept", "Accept-Encoding", "Authorization", "User-Agent")
		}
	}
}

func TestOptionalFieldsOmittedAndExplicitZeroValuesPreserved(t *testing.T) {
	server := contractmock.Start(t, contractPath(t), contractmock.Scenario{})
	client, err := vcfinstaller.NewClient(server.URL(), "omission-token", nil)
	if err != nil {
		t.Fatal(err)
	}
	empty := ""
	proxy := vcfinstaller.ProxyConfiguration{
		IsEnabled:       pointer(false),
		Host:            &empty,
		IsAuthenticated: pointer(false),
	}
	depot := vcfinstaller.DepotSettings{
		VMwareAccount: &vcfinstaller.DepotAccount{DownloadToken: &empty},
		DepotConfiguration: &vcfinstaller.DepotConfiguration{
			IsOfflineDepot: false,
		},
	}
	report, err := client.ConfigureDepotAccess(context.Background(), proxy, depot)
	if err != nil || report.Outcome != vcfinstaller.OutcomeAccepted {
		t.Fatalf("report=%#v error=%v", report, err)
	}
	requests := server.Requests()
	if len(requests) != 3 {
		t.Fatalf("request count = %d, want 3", len(requests))
	}
	wantProxy := `{"isEnabled":false,"host":"","isAuthenticated":false}`
	wantDepot := `{"vmwareAccount":{"downloadToken":""},"depotConfiguration":{"isOfflineDepot":false}}`
	if string(requests[0].Body) != wantProxy {
		t.Fatalf("proxy body = %s, want %s", requests[0].Body, wantProxy)
	}
	if string(requests[1].Body) != wantDepot {
		t.Fatalf("depot body = %s, want %s", requests[1].Body, wantDepot)
	}
	for _, forbidden := range []string{"transferProtocol", "username", "password", "offlineAccount", "hostname", "port", "url", "isConfigured", "status", "message", "null"} {
		if strings.Contains(string(requests[0].Body), forbidden) || strings.Contains(string(requests[1].Body), forbidden) {
			t.Errorf("unset or response-only member %q was serialized: %s %s", forbidden, requests[0].Body, requests[1].Body)
		}
	}
}

func TestFailurePositionReportTable(t *testing.T) {
	tests := []struct {
		name          string
		scenario      contractmock.Scenario
		wantOutcome   vcfinstaller.Outcome
		wantStatuses  []vcfinstaller.StepStatus
		wantHTTP      []int
		wantCalls     int
		wantErrorCode string
	}{
		{
			name:          "first mutation rejected",
			scenario:      contractmock.Scenario{FailOperation: "updateProxyConfiguration", FailStatus: 400},
			wantOutcome:   vcfinstaller.OutcomeFailed,
			wantStatuses:  []vcfinstaller.StepStatus{vcfinstaller.StepFailed, vcfinstaller.StepNotRun, vcfinstaller.StepNotRun},
			wantHTTP:      []int{400, 0, 0},
			wantCalls:     1,
			wantErrorCode: "VCF_PROXY_REJECTED",
		},
		{
			name:          "second mutation rejected",
			scenario:      contractmock.Scenario{FailOperation: "updateDepotSettings", FailStatus: 500},
			wantOutcome:   vcfinstaller.OutcomePartialFailure,
			wantStatuses:  []vcfinstaller.StepStatus{vcfinstaller.StepAccepted, vcfinstaller.StepFailed, vcfinstaller.StepNotRun},
			wantHTTP:      []int{202, 500, 0},
			wantCalls:     2,
			wantErrorCode: "VCF_DEPOT_SETTINGS_FAILED",
		},
		{
			name:         "all calls accepted",
			scenario:     contractmock.Scenario{},
			wantOutcome:  vcfinstaller.OutcomeAccepted,
			wantStatuses: []vcfinstaller.StepStatus{vcfinstaller.StepAccepted, vcfinstaller.StepAccepted, vcfinstaller.StepAccepted},
			wantHTTP:     []int{202, 202, 202},
			wantCalls:    3,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.Start(t, contractPath(t), test.scenario)
			client, err := vcfinstaller.NewClient(server.URL(), "table-token", nil)
			if err != nil {
				t.Fatal(err)
			}
			proxy, depot := bootstrapInputs()
			report, err := client.ConfigureDepotAccess(context.Background(), proxy, depot)
			if report.Outcome != test.wantOutcome || len(report.Steps) != 3 {
				t.Fatalf("report = %#v", report)
			}
			for index := range report.Steps {
				if report.Steps[index].Status != test.wantStatuses[index] || report.Steps[index].HTTPStatus != test.wantHTTP[index] {
					t.Errorf("step %d = %#v, want status=%s HTTP=%d", index, report.Steps[index], test.wantStatuses[index], test.wantHTTP[index])
				}
			}
			if len(server.Requests()) != test.wantCalls {
				t.Fatalf("request count = %d, want %d", len(server.Requests()), test.wantCalls)
			}
			if test.wantErrorCode == "" {
				if err != nil {
					t.Fatalf("unexpected error: %v", err)
				}
			} else {
				var apiError *vcfinstaller.APIError
				if !errors.As(err, &apiError) || apiError.ErrorCode != test.wantErrorCode {
					t.Fatalf("error = %T %#v, want APIError code %s", err, err, test.wantErrorCode)
				}
				failedIndex := test.wantCalls - 1
				if report.Steps[failedIndex].ErrorCode != apiError.ErrorCode || report.Steps[failedIndex].ErrorMessage != apiError.Message {
					t.Fatalf("failed step does not preserve API error: step=%#v error=%#v", report.Steps[failedIndex], apiError)
				}
			}
		})
	}
}

func TestProxyAcceptedResponseProtocolTable(t *testing.T) {
	tests := []struct {
		name        string
		contentType string
		body        string
	}{
		{name: "non JSON media type", contentType: "text/plain", body: `{"id":"task","name":"proxy","status":"IN_PROGRESS","creationTimestamp":"now"}`},
		{name: "malformed JSON", contentType: "application/json", body: `{`},
		{name: "missing id", contentType: "application/json", body: `{"name":"proxy","status":"IN_PROGRESS","creationTimestamp":"now"}`},
		{name: "missing name", contentType: "application/json", body: `{"id":"task","status":"IN_PROGRESS","creationTimestamp":"now"}`},
		{name: "missing status", contentType: "application/json", body: `{"id":"task","name":"proxy","creationTimestamp":"now"}`},
		{name: "missing creation timestamp", contentType: "application/json; charset=utf-8", body: `{"id":"task","name":"proxy","status":"IN_PROGRESS"}`},
		{name: "blank id", contentType: "application/json", body: `{"id":"","name":"proxy","status":"IN_PROGRESS","creationTimestamp":"now"}`},
		{name: "blank name", contentType: "application/json", body: `{"id":"task","name":" ","status":"IN_PROGRESS","creationTimestamp":"now"}`},
		{name: "blank status", contentType: "application/json", body: `{"id":"task","name":"proxy","status":"\t","creationTimestamp":"now"}`},
		{name: "blank creation timestamp", contentType: "application/json", body: `{"id":"task","name":"proxy","status":"IN_PROGRESS","creationTimestamp":"\n"}`},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			var calls atomic.Int32
			transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
				calls.Add(1)
				return response(request, http.StatusAccepted, test.contentType, test.body), nil
			})
			client, err := vcfinstaller.NewClient("https://installer.example", "protocol-token", &http.Client{Transport: transport})
			if err != nil {
				t.Fatal(err)
			}
			proxy, depot := bootstrapInputs()
			report, err := client.ConfigureDepotAccess(context.Background(), proxy, depot)
			var protocolError *vcfinstaller.ProtocolError
			if !errors.As(err, &protocolError) || protocolError.OperationID != "updateProxyConfiguration" {
				t.Fatalf("error = %T %v, want proxy ProtocolError", err, err)
			}
			if calls.Load() != 1 || report.Outcome != vcfinstaller.OutcomeFailed || len(report.Steps) != 3 || report.Steps[0].Status != vcfinstaller.StepFailed || report.Steps[0].HTTPStatus != 202 || report.Steps[1].Status != vcfinstaller.StepNotRun {
				t.Fatalf("calls=%d report=%#v", calls.Load(), report)
			}
		})
	}
}

func TestExactAcceptedStatusRequired(t *testing.T) {
	var calls atomic.Int32
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		calls.Add(1)
		return response(request, http.StatusOK, "application/json", `{}`), nil
	})
	client, err := vcfinstaller.NewClient("https://installer.example", "status-token", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	proxy, depot := bootstrapInputs()
	report, err := client.ConfigureDepotAccess(context.Background(), proxy, depot)
	var apiError *vcfinstaller.APIError
	if !errors.As(err, &apiError) || apiError.OperationID != "updateProxyConfiguration" || apiError.StatusCode != 200 {
		t.Fatalf("error = %T %#v", err, err)
	}
	if calls.Load() != 1 || report.Outcome != vcfinstaller.OutcomeFailed || report.Steps[0].Status != vcfinstaller.StepFailed || report.Steps[0].HTTPStatus != 200 {
		t.Fatalf("calls=%d report=%#v", calls.Load(), report)
	}
}

func TestTransportFailureIsSecretSafeAndReportsPosition(t *testing.T) {
	token := "transport-secret-0213"
	underlying := errors.New("dial failure exposed " + token)
	var calls atomic.Int32
	transport := roundTripFunc(func(*http.Request) (*http.Response, error) {
		calls.Add(1)
		return nil, underlying
	})
	client, err := vcfinstaller.NewClient("https://installer.example", token, &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	proxy, depot := bootstrapInputs()
	report, err := client.ConfigureDepotAccess(context.Background(), proxy, depot)
	var transportError *vcfinstaller.TransportError
	if !errors.As(err, &transportError) || transportError.OperationID != "updateProxyConfiguration" {
		t.Fatalf("error = %T %v, want TransportError", err, err)
	}
	if strings.Contains(err.Error(), token) || strings.Contains(err.Error(), underlying.Error()) {
		t.Fatalf("transport error exposed sensitive underlying text: %v", err)
	}
	if calls.Load() != 1 || report.Outcome != vcfinstaller.OutcomeFailed || report.Steps[0].Status != vcfinstaller.StepFailed || report.Steps[1].Status != vcfinstaller.StepNotRun {
		t.Fatalf("calls=%d report=%#v", calls.Load(), report)
	}
}

func TestRedirectIsRejectedWithoutFollowing(t *testing.T) {
	var calls atomic.Int32
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		calls.Add(1)
		result := response(request, http.StatusTemporaryRedirect, "application/json", `{"errorCode":"REDIRECT","message":"redirects are not accepted"}`)
		result.Header.Set("Location", "https://redirect.example/outside-the-focused-contract")
		return result, nil
	})
	client, err := vcfinstaller.NewClient("https://installer.example", "redirect-token", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	proxy, depot := bootstrapInputs()
	report, err := client.ConfigureDepotAccess(context.Background(), proxy, depot)
	var apiError *vcfinstaller.APIError
	if !errors.As(err, &apiError) || apiError.OperationID != "updateProxyConfiguration" || apiError.StatusCode != http.StatusTemporaryRedirect {
		t.Fatalf("error = %T %#v, want proxy APIError for redirect", err, err)
	}
	if calls.Load() != 1 || report.Outcome != vcfinstaller.OutcomeFailed || report.Steps[0].Status != vcfinstaller.StepFailed || report.Steps[0].HTTPStatus != http.StatusTemporaryRedirect || report.Steps[1].Status != vcfinstaller.StepNotRun {
		t.Fatalf("calls=%d report=%#v", calls.Load(), report)
	}
}

func TestDeadlineErrorIsPreserved(t *testing.T) {
	var calls atomic.Int32
	transport := roundTripFunc(func(*http.Request) (*http.Response, error) {
		calls.Add(1)
		return nil, context.DeadlineExceeded
	})
	client, err := vcfinstaller.NewClient("https://installer.example", "deadline-token", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	proxy, depot := bootstrapInputs()
	report, err := client.ConfigureDepotAccess(context.Background(), proxy, depot)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("error = %T %v, want preserved deadline", err, err)
	}
	if calls.Load() != 1 || report.Outcome != vcfinstaller.OutcomeFailed || report.Steps[0].Status != vcfinstaller.StepFailed || report.Steps[1].Status != vcfinstaller.StepNotRun {
		t.Fatalf("calls=%d report=%#v", calls.Load(), report)
	}
}

func TestAcceptedAndRejectedResponseBodiesAreClosed(t *testing.T) {
	bodies := []*trackingBody{
		{Reader: strings.NewReader(`{"id":"task","name":"proxy","status":"IN_PROGRESS","creationTimestamp":"now"}`)},
		{Reader: strings.NewReader(`{"errorCode":"REJECTED","message":"depot rejected"}`)},
	}
	var calls atomic.Int32
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		index := int(calls.Add(1)) - 1
		status := http.StatusAccepted
		if index == 1 {
			status = http.StatusInternalServerError
		}
		return &http.Response{
			StatusCode: status,
			Header:     http.Header{"Content-Type": []string{"application/json"}},
			Body:       bodies[index],
			Request:    request,
		}, nil
	})
	client, err := vcfinstaller.NewClient("https://installer.example", "body-close-token", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	proxy, depot := bootstrapInputs()
	report, err := client.ConfigureDepotAccess(context.Background(), proxy, depot)
	var apiError *vcfinstaller.APIError
	if !errors.As(err, &apiError) || report.Outcome != vcfinstaller.OutcomePartialFailure || calls.Load() != 2 {
		t.Fatalf("calls=%d report=%#v error=%T %v", calls.Load(), report, err, err)
	}
	for index, body := range bodies {
		if !body.closed.Load() {
			t.Errorf("response body %d was not closed", index+1)
		}
	}
}

func TestContextAndConstructorValidationTable(t *testing.T) {
	token := "constructor-secret-0213"
	invalid := []struct {
		name    string
		baseURL string
		token   string
	}{
		{name: "relative URL", baseURL: "installer.example", token: token},
		{name: "non HTTP scheme", baseURL: "ftp://installer.example", token: token},
		{name: "missing host", baseURL: "https:///v1", token: token},
		{name: "userinfo", baseURL: "https://user@installer.example", token: token},
		{name: "non root path", baseURL: "https://installer.example/api", token: token},
		{name: "query", baseURL: "https://installer.example?x=1", token: token},
		{name: "bare query", baseURL: "https://installer.example?", token: token},
		{name: "fragment", baseURL: "https://installer.example#fragment", token: token},
		{name: "blank token", baseURL: "https://installer.example", token: " \t"},
		{name: "CR token", baseURL: "https://installer.example", token: "secret\rvalue"},
		{name: "LF token", baseURL: "https://installer.example", token: "secret\nvalue"},
		{name: "NUL token", baseURL: "https://installer.example", token: "secret\x00value"},
		{name: "SOH token", baseURL: "https://installer.example", token: "secret\x01value"},
		{name: "DEL token", baseURL: "https://installer.example", token: "secret\x7fvalue"},
	}
	for _, test := range invalid {
		t.Run(test.name, func(t *testing.T) {
			client, err := vcfinstaller.NewClient(test.baseURL, test.token, nil)
			if err == nil || client != nil {
				t.Fatalf("NewClient(%q) = %#v, %v; want rejection", test.baseURL, client, err)
			}
			if strings.Contains(err.Error(), token) || strings.Contains(err.Error(), test.token) && test.token != "" {
				t.Fatalf("validation error exposed token: %v", err)
			}
		})
	}
	for _, baseURL := range []string{"http://127.0.0.1:8080", "HTTPS://installer.example/"} {
		if _, err := vcfinstaller.NewClient(baseURL, "valid-token", nil); err != nil {
			t.Errorf("valid root %q rejected: %v", baseURL, err)
		}
	}
	if _, err := vcfinstaller.NewClient("https://installer.example", "valid\ttoken", nil); err != nil {
		t.Errorf("header-safe horizontal tab was rejected: %v", err)
	}

	var calls atomic.Int32
	transport := roundTripFunc(func(*http.Request) (*http.Response, error) {
		calls.Add(1)
		return nil, errors.New("must not be called")
	})
	client, err := vcfinstaller.NewClient("https://installer.example", "nil-context-token", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	proxy, depot := bootstrapInputs()
	report, err := client.ConfigureDepotAccess(nil, proxy, depot)
	if err == nil || calls.Load() != 0 || report.Outcome != vcfinstaller.OutcomeFailed || len(report.Steps) != 3 {
		t.Fatalf("nil context calls=%d report=%#v error=%v", calls.Load(), report, err)
	}
	for _, step := range report.Steps {
		if step.Status != vcfinstaller.StepNotRun {
			t.Fatalf("nil-context step = %#v, want NotRun", step)
		}
	}
}

func TestContextCancellationIsPreserved(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		cancel()
		<-request.Context().Done()
		return nil, request.Context().Err()
	})
	client, err := vcfinstaller.NewClient("https://installer.example", "context-token", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	proxy, depot := bootstrapInputs()
	report, err := client.ConfigureDepotAccess(ctx, proxy, depot)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error = %T %v, want preserved cancellation", err, err)
	}
	if report.Outcome != vcfinstaller.OutcomeFailed || report.Steps[0].Status != vcfinstaller.StepFailed || report.Steps[1].Status != vcfinstaller.StepNotRun {
		t.Fatalf("report = %#v", report)
	}
}

func assertSingleHeader(t *testing.T, header http.Header, name, want string) {
	t.Helper()
	values := header.Values(name)
	if len(values) != 1 || values[0] != want {
		t.Errorf("%s values = %q, want exactly [%q]", name, values, want)
	}
}

func assertHeaderNames(t *testing.T, header http.Header, want ...string) {
	t.Helper()
	got := make([]string, 0, len(header))
	for name := range header {
		got = append(got, name)
	}
	sort.Strings(got)
	sort.Strings(want)
	if !reflect.DeepEqual(got, want) {
		t.Errorf("header names = %v, want %v", got, want)
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return f(request)
}

type trackingBody struct {
	io.Reader
	closed atomic.Bool
}

func (b *trackingBody) Close() error {
	b.closed.Store(true)
	return nil
}

func response(request *http.Request, status int, contentType, body string) *http.Response {
	header := make(http.Header)
	if contentType != "" {
		header.Set("Content-Type", contentType)
	}
	return &http.Response{
		StatusCode: status,
		Header:     header,
		Body:       io.NopCloser(strings.NewReader(body)),
		Request:    request,
	}
}
