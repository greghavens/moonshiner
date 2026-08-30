package verification_test

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"example.com/vcf-installer-client/internal/contractmock"
	"example.com/vcf-installer-client/vcfinstaller"
)

const (
	wantCommit = "c3f3b52c845dd967cabbc21680e893292077d5ba"
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

func TestOfficialSourceRecordsEveryContractOperation(t *testing.T) {
	readJSON := func(name string, destination any) {
		t.Helper()
		data, err := os.ReadFile(filepath.Join(repositoryRoot(t), name))
		if err != nil {
			t.Fatalf("read %s: %v", name, err)
		}
		if err := json.Unmarshal(data, destination); err != nil {
			t.Fatalf("decode %s: %v", name, err)
		}
	}
	type source struct {
		RepositoryCommitSHA string `json:"repositoryCommitSha"`
		SpecPath            string `json:"specPath"`
	}
	type operation struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
		Parameters  []struct {
			Name     string `json:"name"`
			In       string `json:"in"`
			Required bool   `json:"required"`
			Schema   struct {
				Type string `json:"type"`
			} `json:"schema"`
		} `json:"parameters"`
		Responses map[string]struct {
			Description string `json:"description"`
		} `json:"responses"`
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
		} `json:"operations"`
	}
	readJSON("docs/contract.json", &contract)
	readJSON("docs/official_sources.json", &official)

	if contract.Source.RepositoryCommitSHA != wantCommit || contract.Source.SpecPath != wantSpec {
		t.Fatalf("contract source = %+v", contract.Source)
	}
	if official.RepositoryCommitSHA != wantCommit || official.SpecPath != wantSpec {
		t.Fatalf("official source = %s %s", official.RepositoryCommitSHA, official.SpecPath)
	}
	if len(contract.Operations) != 1 || len(official.Operations) != 1 || len(official.OperationIDs) != 1 {
		t.Fatalf("operation counts contract=%d official=%d ids=%d, want one", len(contract.Operations), len(official.Operations), len(official.OperationIDs))
	}
	wantID, wantMethod, wantPath := "deleteDepotSettings", http.MethodDelete, "/v1/system/settings/depot"
	got := contract.Operations[0]
	if got.OperationID != wantID || got.Method != wantMethod || got.Path != wantPath {
		t.Fatalf("contract operation = %+v", got)
	}
	if len(got.Parameters) != 1 || got.Parameters[0].Name != "depotType" || got.Parameters[0].In != "query" || got.Parameters[0].Required || got.Parameters[0].Schema.Type != "string" {
		t.Fatalf("depotType projection = %+v", got.Parameters)
	}
	if len(got.Responses) != 3 || got.Responses["204"].Description != "No Content" || got.Responses["400"].Description != "Bad Request" || got.Responses["500"].Description != "Internal Server Error" {
		t.Fatalf("response projection = %+v", got.Responses)
	}
	recorded := official.Operations[0]
	if official.OperationIDs[0] != wantID || recorded.OperationID != wantID || recorded.Method != wantMethod || recorded.Path != wantPath || recorded.RepositoryCommitSHA != wantCommit || recorded.SpecPath != wantSpec {
		t.Fatalf("official operation record = %+v ids=%v", recorded, official.OperationIDs)
	}
}

func TestAmbiguousDeleteRetriesWithoutDuplicatingEffect(t *testing.T) {
	server := contractmock.Start(t, contractPath(t), true)
	token := "runtime-token-0212"
	client, err := vcfinstaller.NewClient(server.URL(), token, &http.Client{Timeout: 2 * time.Second})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if err := client.DeleteDepotSettings(context.Background(), vcfinstaller.DeleteOptions{MaxAttempts: 2}); err != nil {
		t.Fatalf("DeleteDepotSettings: %v", err)
	}

	requests := server.Requests()
	if len(requests) != 2 {
		t.Fatalf("request log = %v, want two identical DELETE attempts", requests)
	}
	if effects := server.EffectCount(); effects != 1 {
		t.Fatalf("semantic delete effects = %d, want exactly one", effects)
	}
	for index, request := range requests {
		if request.OperationID != "deleteDepotSettings" || request.Method != http.MethodDelete {
			t.Fatalf("attempt %d = %v", index+1, request)
		}
		if request.RawTarget != "/v1/system/settings/depot" {
			t.Fatalf("attempt %d raw target = %q; unset depotType must not produce an empty query or bare ?", index+1, request.RawTarget)
		}
		assertSingleHeader(t, request.Header, "Authorization", "Bearer "+token)
		assertSingleHeader(t, request.Header, "Accept", "application/json")
		assertHeaderNames(t, request.Header, "Accept", "Accept-Encoding", "Authorization", "User-Agent")
		if values := request.Header.Values("Content-Type"); len(values) != 0 {
			t.Errorf("attempt %d Content-Type values = %v, want absent", index+1, values)
		}
		if len(request.Body) != 0 || request.ContentLength != 0 || len(request.TransferEncoding) != 0 {
			t.Errorf("attempt %d framing body=%d contentLength=%d transferEncoding=%v", index+1, len(request.Body), request.ContentLength, request.TransferEncoding)
		}
	}
	if requests[0].RawTarget != requests[1].RawTarget {
		t.Fatalf("retry target changed: %q then %q", requests[0].RawTarget, requests[1].RawTarget)
	}
}

func TestDepotTypeOptionalWireTable(t *testing.T) {
	empty := ""
	vcfDepot := "VCF_DEPOT"
	special := "VCF DEPOT/primary"
	tests := []struct {
		name      string
		depotType *string
		want      string
	}{
		{name: "unset is omitted", depotType: nil, want: "/v1/system/settings/depot"},
		{name: "explicit empty remains present", depotType: &empty, want: "/v1/system/settings/depot?depotType="},
		{name: "named depot", depotType: &vcfDepot, want: "/v1/system/settings/depot?depotType=VCF_DEPOT"},
		{name: "query escaping", depotType: &special, want: "/v1/system/settings/depot?depotType=VCF+DEPOT%2Fprimary"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.Start(t, contractPath(t), false)
			client, err := vcfinstaller.NewClient(server.URL(), "optional-token", nil)
			if err != nil {
				t.Fatal(err)
			}
			if err := client.DeleteDepotSettings(context.Background(), vcfinstaller.DeleteOptions{DepotType: test.depotType, MaxAttempts: 1}); err != nil {
				t.Fatalf("DeleteDepotSettings: %v", err)
			}
			requests := server.Requests()
			if len(requests) != 1 || requests[0].RawTarget != test.want {
				t.Fatalf("request log = %v, want raw target %q", requests, test.want)
			}
			if server.EffectCount() != 1 {
				t.Fatalf("effect count = %d, want one", server.EffectCount())
			}
		})
	}
}

func TestStatusRetryTable(t *testing.T) {
	tests := []struct {
		name         string
		statuses     []int
		maxAttempts  int
		wantCalls    int32
		wantStatus   int
		wantAttempts int
	}{
		{name: "declared 500 is retried", statuses: []int{500, 204}, maxAttempts: 2, wantCalls: 2},
		{name: "400 is final", statuses: []int{400, 204}, maxAttempts: 2, wantCalls: 1, wantStatus: 400, wantAttempts: 1},
		{name: "undeclared 503 is final", statuses: []int{503, 204}, maxAttempts: 2, wantCalls: 1, wantStatus: 503, wantAttempts: 1},
		{name: "other success is rejected", statuses: []int{200, 204}, maxAttempts: 2, wantCalls: 1, wantStatus: 200, wantAttempts: 1},
		{name: "500 exhaustion returns last API error", statuses: []int{500, 500, 204}, maxAttempts: 2, wantCalls: 2, wantStatus: 500, wantAttempts: 2},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			var calls atomic.Int32
			var bodies []*trackingBody
			transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
				index := int(calls.Add(1)) - 1
				status := test.statuses[index]
				body := &trackingBody{}
				bodies = append(bodies, body)
				result := response(request, status)
				result.Body = body
				return result, nil
			})
			client, err := vcfinstaller.NewClient("https://installer.example", "status-token", &http.Client{Transport: transport})
			if err != nil {
				t.Fatal(err)
			}
			err = client.DeleteDepotSettings(context.Background(), vcfinstaller.DeleteOptions{MaxAttempts: test.maxAttempts})
			if got := calls.Load(); got != test.wantCalls {
				t.Fatalf("calls = %d, want %d", got, test.wantCalls)
			}
			var api *vcfinstaller.APIError
			if test.wantStatus == 0 {
				if err != nil {
					t.Fatalf("unexpected error: %v", err)
				}
			} else if !errors.As(err, &api) || api.OperationID != "deleteDepotSettings" || api.StatusCode != test.wantStatus || api.Attempts != test.wantAttempts {
				t.Fatalf("error = %T %v, want APIError status=%d attempts=%d", err, err, test.wantStatus, test.wantAttempts)
			}
			for index, body := range bodies {
				if !body.closed.Load() {
					t.Errorf("response body %d was not closed", index+1)
				}
			}
		})
	}
}

func TestRedirectIsFinalAndDoesNotBroadenDelete(t *testing.T) {
	var calls atomic.Int32
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		call := calls.Add(1)
		if call == 1 {
			redirect := response(request, http.StatusTemporaryRedirect)
			redirect.Header.Set("Location", "https://other.example/outside-the-contract")
			return redirect, nil
		}
		return response(request, http.StatusNoContent), nil
	})
	client, err := vcfinstaller.NewClient("https://installer.example", "redirect-token", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	err = client.DeleteDepotSettings(context.Background(), vcfinstaller.DeleteOptions{MaxAttempts: 2})
	var api *vcfinstaller.APIError
	if !errors.As(err, &api) || api.OperationID != "deleteDepotSettings" || api.StatusCode != http.StatusTemporaryRedirect || api.Attempts != 1 {
		t.Fatalf("error = %T %v, want final APIError status=%d attempts=1", err, err, http.StatusTemporaryRedirect)
	}
	if got := calls.Load(); got != 1 {
		t.Fatalf("transport calls = %d, want one; redirect must not broaden the DELETE", got)
	}
}

func TestResponseBodiesAreClosedOnRetryAndSuccess(t *testing.T) {
	var calls atomic.Int32
	var bodies []*trackingBody
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		body := &trackingBody{}
		bodies = append(bodies, body)
		status := http.StatusInternalServerError
		if calls.Add(1) == 2 {
			status = http.StatusNoContent
		}
		result := response(request, status)
		result.Body = body
		return result, nil
	})
	client, err := vcfinstaller.NewClient("https://installer.example", "close-token", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	if err := client.DeleteDepotSettings(context.Background(), vcfinstaller.DeleteOptions{MaxAttempts: 2}); err != nil {
		t.Fatalf("DeleteDepotSettings: %v", err)
	}
	if len(bodies) != 2 {
		t.Fatalf("response bodies = %d, want two", len(bodies))
	}
	for index, body := range bodies {
		if !body.closed.Load() {
			t.Errorf("response body %d was not closed", index+1)
		}
	}
}

func TestTransportFailureRetryAndSecretSafety(t *testing.T) {
	token := "transport-secret-0212"
	diagnostic := "private-dialer-diagnostic-0212"
	underlying := errors.New(diagnostic + ": transport echoed " + token)
	var calls atomic.Int32
	transport := roundTripFunc(func(*http.Request) (*http.Response, error) {
		calls.Add(1)
		return nil, underlying
	})
	client, err := vcfinstaller.NewClient("https://installer.example", token, &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	err = client.DeleteDepotSettings(context.Background(), vcfinstaller.DeleteOptions{MaxAttempts: 3})
	var transportError *vcfinstaller.TransportError
	if !errors.As(err, &transportError) || transportError.OperationID != "deleteDepotSettings" || transportError.Attempts != 3 {
		t.Fatalf("error = %T %v, want TransportError after three attempts", err, err)
	}
	if calls.Load() != 3 {
		t.Fatalf("transport calls = %d, want 3", calls.Load())
	}
	if strings.Contains(err.Error(), token) {
		t.Fatalf("transport error exposed the access token: %v", err)
	}
	if strings.Contains(err.Error(), diagnostic) {
		t.Fatalf("transport error exposed the underlying error string: %v", err)
	}
}

func TestContextCancellationInterruptsRetryDelay(t *testing.T) {
	firstAttempt := make(chan struct{}, 1)
	var calls atomic.Int32
	transport := roundTripFunc(func(*http.Request) (*http.Response, error) {
		if calls.Add(1) == 1 {
			firstAttempt <- struct{}{}
		}
		return nil, errors.New("ambiguous transport failure")
	})
	client, err := vcfinstaller.NewClient("https://installer.example", "cancel-token", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result := make(chan error, 1)
	go func() {
		result <- client.DeleteDepotSettings(ctx, vcfinstaller.DeleteOptions{MaxAttempts: 5, RetryDelay: time.Hour})
	}()
	select {
	case <-firstAttempt:
	case <-time.After(2 * time.Second):
		t.Fatal("first attempt did not run")
	}
	cancel()
	select {
	case callErr := <-result:
		if !errors.Is(callErr, context.Canceled) {
			t.Fatalf("error = %T %v, want context.Canceled", callErr, callErr)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("context cancellation did not interrupt retry delay")
	}
	if calls.Load() != 1 {
		t.Fatalf("calls after cancellation = %d, want one", calls.Load())
	}
}

func TestExpiredContextPreservesDeadlineWithoutSending(t *testing.T) {
	var calls atomic.Int32
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		calls.Add(1)
		return response(request, http.StatusNoContent), nil
	})
	client, err := vcfinstaller.NewClient("https://installer.example", "deadline-token", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithDeadline(context.Background(), time.Unix(1, 0))
	defer cancel()
	err = client.DeleteDepotSettings(ctx, vcfinstaller.DeleteOptions{MaxAttempts: 1})
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("error = %T %v, want context.DeadlineExceeded", err, err)
	}
	if got := calls.Load(); got != 0 {
		t.Fatalf("expired context sent %d requests, want none", got)
	}
}

func TestNewClientValidationTable(t *testing.T) {
	tests := []struct {
		name, baseURL, token string
	}{
		{name: "empty URL", token: "token"},
		{name: "non HTTP scheme", baseURL: "ftp://installer.example", token: "token"},
		{name: "missing host", baseURL: "https:///v1", token: "token"},
		{name: "userinfo", baseURL: "https://user:password@installer.example", token: "token"},
		{name: "non-root path", baseURL: "https://installer.example/base", token: "token"},
		{name: "query", baseURL: "https://installer.example?debug=true", token: "token"},
		{name: "bare query", baseURL: "https://installer.example?", token: "token"},
		{name: "fragment", baseURL: "https://installer.example#fragment", token: "token"},
		{name: "empty fragment", baseURL: "https://installer.example#", token: "token"},
		{name: "empty token", baseURL: "https://installer.example"},
		{name: "blank token", baseURL: "https://installer.example", token: " \t "},
		{name: "carriage return", baseURL: "https://installer.example", token: "secret\rinjected"},
		{name: "line feed", baseURL: "https://installer.example", token: "secret\ninjected"},
		{name: "NUL", baseURL: "https://installer.example", token: "secret\x00injected"},
		{name: "other control byte", baseURL: "https://installer.example", token: "secret\x01injected"},
		{name: "DEL", baseURL: "https://installer.example", token: "secret\x7finjected"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			client, err := vcfinstaller.NewClient(test.baseURL, test.token, nil)
			if err == nil || client != nil {
				t.Fatalf("NewClient(%q, token) = (%v, %v), want nil client and error", test.baseURL, client, err)
			}
			if test.token != "" && strings.Contains(err.Error(), test.token) {
				t.Fatalf("error exposed access token %q: %v", test.token, err)
			}
		})
	}
	if client, err := vcfinstaller.NewClient("HTTP://installer.example/", "valid\ttoken", nil); err != nil || client == nil {
		t.Fatalf("valid service root and header value rejected: client=%v error=%v", client, err)
	}
}

func TestArgumentErrorsDoNotSendRequests(t *testing.T) {
	var calls atomic.Int32
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		calls.Add(1)
		return response(request, http.StatusNoContent), nil
	})
	client, err := vcfinstaller.NewClient("https://installer.example", "argument-token", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	tests := []struct {
		name    string
		ctx     context.Context
		options vcfinstaller.DeleteOptions
	}{
		{name: "nil context", ctx: nil, options: vcfinstaller.DeleteOptions{MaxAttempts: 1}},
		{name: "negative attempts", ctx: context.Background(), options: vcfinstaller.DeleteOptions{MaxAttempts: -1}},
		{name: "zero attempts", ctx: context.Background(), options: vcfinstaller.DeleteOptions{}},
		{name: "too many attempts", ctx: context.Background(), options: vcfinstaller.DeleteOptions{MaxAttempts: 6}},
		{name: "negative delay", ctx: context.Background(), options: vcfinstaller.DeleteOptions{MaxAttempts: 1, RetryDelay: -time.Nanosecond}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if err := client.DeleteDepotSettings(test.ctx, test.options); err == nil {
				t.Fatal("invalid call returned nil error")
			}
		})
	}
	if calls.Load() != 0 {
		t.Fatalf("invalid calls sent %d requests", calls.Load())
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

type trackingBody struct {
	closed atomic.Bool
}

func (*trackingBody) Read([]byte) (int, error) { return 0, io.EOF }

func (body *trackingBody) Close() error {
	body.closed.Store(true)
	return nil
}

func response(request *http.Request, status int) *http.Response {
	return &http.Response{
		StatusCode: status,
		Header:     make(http.Header),
		Body:       io.NopCloser(strings.NewReader("")),
		Request:    request,
	}
}

func assertHeaderNames(t *testing.T, header http.Header, allowedNames ...string) {
	t.Helper()
	allowed := make(map[string]bool, len(allowedNames))
	for _, name := range allowedNames {
		allowed[http.CanonicalHeaderKey(name)] = true
	}
	for name := range header {
		if !allowed[http.CanonicalHeaderKey(name)] {
			t.Errorf("unexpected request header %q", name)
		}
	}
}

func assertSingleHeader(t *testing.T, header http.Header, name, want string) {
	t.Helper()
	values := header.Values(name)
	if len(values) != 1 || values[0] != want {
		t.Errorf("%s values = %q, want exactly [%q]", name, values, want)
	}
	for _, value := range values {
		if strings.ContainsAny(value, "\r\n") {
			t.Errorf("%s contains a line break", name)
		}
	}
}
