package verification_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
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
	wantCommit        = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	wantInstallerSpec = "specifications/vcf-installer/vcf-installer-openapi.json"
	wantManagerSpec   = "specifications/sddc-manager/sddc-manager-openapi.json"
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
	}
	type operation struct {
		OperationID   string         `json:"operationId"`
		Method        string         `json:"method"`
		Path          string         `json:"path"`
		SuccessStatus int            `json:"successStatus"`
		Responses     map[string]any `json:"responses"`
	}
	var contract struct {
		Sources    []source    `json:"sources"`
		Operations []operation `json:"operations"`
	}
	var official struct {
		Sources []struct {
			RepositoryCommitSHA string      `json:"repositoryCommitSha"`
			SpecPath            string      `json:"specPath"`
			Operations          []operation `json:"operations"`
		} `json:"sources"`
	}
	readJSON(t, "docs/contract.json", &contract)
	readJSON(t, "docs/official_sources.json", &official)

	wantPaths := map[string]bool{wantInstallerSpec: true, wantManagerSpec: true}
	for _, sources := range [][]source{contract.Sources} {
		if len(sources) != 2 {
			t.Fatalf("contract sources = %+v", sources)
		}
		for _, got := range sources {
			if got.RepositoryCommitSHA != wantCommit || !wantPaths[got.SpecPath] {
				t.Fatalf("contract source = %+v", got)
			}
		}
	}
	want := []struct {
		id     string
		method string
		path   string
		status int
	}{
		{"updateProxyConfiguration", http.MethodPatch, "/v1/system/proxy-configuration", 202},
		{"updateServicesConfig", http.MethodPut, "/v1/services-config", 200},
		{"syncDepotMetadata", http.MethodPatch, "/v1/system/settings/depot/depot-sync-info", 202},
	}
	if len(contract.Operations) != len(want) {
		t.Fatalf("contract operation count=%d", len(contract.Operations))
	}
	for index, expected := range want {
		got := contract.Operations[index]
		if got.OperationID != expected.id || got.Method != expected.method || got.Path != expected.path || got.SuccessStatus != expected.status {
			t.Fatalf("contract operation %d = %+v", index, got)
		}
		if _, ok := got.Responses[fmt.Sprint(expected.status)]; !ok {
			t.Fatalf("contract responses for %s = %+v", got.OperationID, got.Responses)
		}
	}
	seen := map[string]bool{}
	for _, source := range official.Sources {
		if source.RepositoryCommitSHA != wantCommit || !wantPaths[source.SpecPath] {
			t.Fatalf("official source = %+v", source)
		}
		for _, operation := range source.Operations {
			seen[operation.OperationID] = true
		}
	}
	if len(seen) != 3 {
		t.Fatalf("official operations = %v", seen)
	}
}

func pointer[T any](value T) *T { return &value }

func bootstrapInputs() (vcfinstaller.ProxyConfiguration, vcfinstaller.ServicesConfig) {
	return vcfinstaller.ProxyConfiguration{
			IsEnabled:        pointer(true),
			Host:             pointer("proxy.bootstrap.example"),
			Port:             pointer[int32](8443),
			TransferProtocol: pointer("HTTPS"),
		}, vcfinstaller.ServicesConfig{
			Services: []vcfinstaller.ServiceConfig{{
				Name: "VCF Depot", Type: "VCF_DEPOT", Key: "depot-service-key",
				Nodes: []vcfinstaller.ServiceNode{{
					Name:      "VCF Depot",
					Addresses: []vcfinstaller.ServiceNodeAddress{{Type: "Fqdn", Value: "vcf-flt01.vcf.lab"}},
				}},
			}},
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
	proxy, services := bootstrapInputs()
	report, err := client.ConfigureDepotAccess(context.Background(), proxy, services)

	wantReport := vcfinstaller.ChangeReport{
		Outcome: vcfinstaller.OutcomePartialFailure,
		Steps: []vcfinstaller.StepResult{
			{OperationID: "updateProxyConfiguration", Status: vcfinstaller.StepAccepted, HTTPStatus: 202, TaskID: "task-proxy-0213"},
			{OperationID: "updateServicesConfig", Status: vcfinstaller.StepAccepted, HTTPStatus: 200},
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
		[]byte(`{"services":[{"name":"VCF Depot","type":"VCF_DEPOT","key":"depot-service-key","nodes":[{"name":"VCF Depot","addresses":[{"type":"Fqdn","value":"vcf-flt01.vcf.lab"}]}]}]}`),
		nil,
	}
	wantMethods := []string{http.MethodPatch, http.MethodPut, http.MethodPatch}
	wantTargets := []string{
		"/v1/system/proxy-configuration",
		"/v1/services-config",
		"/v1/system/settings/depot/depot-sync-info",
	}
	wantIDs := []string{"updateProxyConfiguration", "updateServicesConfig", "syncDepotMetadata"}
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
			// Content-Length is not the caller's to decide here: net/http sends
			// `Content-Length: 0` on a bodiless PATCH whether the request was built
			// from a nil body or an empty reader, and the server keeps it in the
			// header map. The framing check above already pins the length to zero,
			// so listing the header either way judges the standard library rather
			// than the client.
			assertHeaderNamesIgnoring(t, request.Header, "Content-Length", "Accept", "Accept-Encoding", "Authorization", "User-Agent")
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
	services := vcfinstaller.ServicesConfig{Services: []vcfinstaller.ServiceConfig{{
		Name: empty, Type: "VCF_DEPOT", Key: empty,
		Nodes: []vcfinstaller.ServiceNode{{
			Name:      empty,
			Addresses: []vcfinstaller.ServiceNodeAddress{{Type: "Fqdn", Value: empty}},
		}},
	}}}
	report, err := client.ConfigureDepotAccess(context.Background(), proxy, services)
	if err != nil || report.Outcome != vcfinstaller.OutcomeAccepted {
		t.Fatalf("report=%#v error=%v", report, err)
	}
	requests := server.Requests()
	if len(requests) != 3 {
		t.Fatalf("request count = %d, want 3", len(requests))
	}
	wantProxy := `{"isEnabled":false,"host":"","isAuthenticated":false}`
	wantServices := `{"services":[{"name":"","type":"VCF_DEPOT","key":"","nodes":[{"name":"","addresses":[{"type":"Fqdn","value":""}]}]}]}`
	if string(requests[0].Body) != wantProxy {
		t.Fatalf("proxy body = %s, want %s", requests[0].Body, wantProxy)
	}
	if string(requests[1].Body) != wantServices {
		t.Fatalf("services body = %s, want %s", requests[1].Body, wantServices)
	}
	for _, forbidden := range []string{"transferProtocol", "username", "password", "version", "port", "baseUrl", "certificates", "isConfigured", "status", "message", "null"} {
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
			scenario:      contractmock.Scenario{FailOperation: "updateServicesConfig", FailStatus: 500},
			wantOutcome:   vcfinstaller.OutcomePartialFailure,
			wantStatuses:  []vcfinstaller.StepStatus{vcfinstaller.StepAccepted, vcfinstaller.StepFailed, vcfinstaller.StepNotRun},
			wantHTTP:      []int{202, 500, 0},
			wantCalls:     2,
			wantErrorCode: "VCF_SERVICES_CONFIG_FAILED",
		},
		{
			name:         "all calls accepted",
			scenario:     contractmock.Scenario{},
			wantOutcome:  vcfinstaller.OutcomeAccepted,
			wantStatuses: []vcfinstaller.StepStatus{vcfinstaller.StepAccepted, vcfinstaller.StepAccepted, vcfinstaller.StepAccepted},
			wantHTTP:     []int{202, 200, 202},
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
			proxy, services := bootstrapInputs()
			report, err := client.ConfigureDepotAccess(context.Background(), proxy, services)
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
			proxy, services := bootstrapInputs()
			report, err := client.ConfigureDepotAccess(context.Background(), proxy, services)
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
	proxy, services := bootstrapInputs()
	report, err := client.ConfigureDepotAccess(context.Background(), proxy, services)
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
	proxy, services := bootstrapInputs()
	report, err := client.ConfigureDepotAccess(context.Background(), proxy, services)
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
	proxy, services := bootstrapInputs()
	report, err := client.ConfigureDepotAccess(context.Background(), proxy, services)
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
	proxy, services := bootstrapInputs()
	report, err := client.ConfigureDepotAccess(context.Background(), proxy, services)
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
	proxy, services := bootstrapInputs()
	report, err := client.ConfigureDepotAccess(context.Background(), proxy, services)
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
	proxy, services := bootstrapInputs()
	report, err := client.ConfigureDepotAccess(nil, proxy, services)
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
	proxy, services := bootstrapInputs()
	report, err := client.ConfigureDepotAccess(ctx, proxy, services)
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

func assertHeaderNamesIgnoring(t *testing.T, header http.Header, ignore string, want ...string) {
	t.Helper()
	kept := make(http.Header, len(header))
	for name, values := range header {
		if http.CanonicalHeaderKey(name) == http.CanonicalHeaderKey(ignore) {
			continue
		}
		kept[name] = values
	}
	assertHeaderNames(t, kept, want...)
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
