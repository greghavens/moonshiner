package verification_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
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

func TestOfficialSourceRecordsEveryContractOperation(t *testing.T) {
	root := repositoryRoot(t)
	readJSON := func(name string, destination any) {
		t.Helper()
		data, err := os.ReadFile(filepath.Join(root, name))
		if err != nil {
			t.Fatalf("read %s: %v", name, err)
		}
		if err := json.Unmarshal(data, destination); err != nil {
			t.Fatalf("decode %s: %v", name, err)
		}
	}
	var contract struct {
		Source struct {
			RepositoryCommitSHA string `json:"repositoryCommitSha"`
			SpecPath            string `json:"specPath"`
		} `json:"source"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
		} `json:"operations"`
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
	readJSON("docs/contract.json", &contract)
	readJSON("docs/official_sources.json", &sources)
	if contract.Source.RepositoryCommitSHA != wantCommit || contract.Source.SpecPath != wantSpec {
		t.Fatalf("contract source = %s %s", contract.Source.RepositoryCommitSHA, contract.Source.SpecPath)
	}
	if sources.RepositoryCommitSHA != wantCommit || sources.SpecPath != wantSpec {
		t.Fatalf("official source = %s %s", sources.RepositoryCommitSHA, sources.SpecPath)
	}
	want := []struct {
		id, method, path string
	}{
		{"updateProxyConfiguration", http.MethodPatch, "/v1/system/proxy-configuration"},
		{"getTask", http.MethodGet, "/v1/tasks/{id}"},
	}
	if len(contract.Operations) != len(want) || len(sources.Operations) != len(want) || len(sources.OperationIDs) != len(want) {
		t.Fatalf("operation counts contract=%d sources=%d ids=%d want=%d", len(contract.Operations), len(sources.Operations), len(sources.OperationIDs), len(want))
	}
	for index, expected := range want {
		t.Run(expected.id, func(t *testing.T) {
			operation := contract.Operations[index]
			if operation.OperationID != expected.id || operation.Method != expected.method || operation.Path != expected.path {
				t.Fatalf("contract operation = %+v, want %+v", operation, expected)
			}
			source := sources.Operations[index]
			if source.OperationID != expected.id || source.RepositoryCommitSHA != wantCommit || source.SpecPath != wantSpec {
				t.Fatalf("official source operation = %+v", source)
			}
			if sources.OperationIDs[index] != expected.id {
				t.Fatalf("official operationIds[%d] = %q, want %q", index, sources.OperationIDs[index], expected.id)
			}
		})
	}
}

func TestUpdateProxyAndWaitExactWire(t *testing.T) {
	server := contractmock.Start(t, filepath.Join(repositoryRoot(t), "docs", "contract.json"), []string{
		"PENDING",
		"In Progress",
		"Successful",
	})
	token := "runtime-token-0209"
	client, err := vcfinstaller.NewClient(server.URL(), token, &http.Client{Timeout: 2 * time.Second})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	host := "proxy.runtime.lab"
	port := int32(3128)
	final, err := client.UpdateProxyAndWait(context.Background(), vcfinstaller.ProxyConfiguration{
		IsEnabled: true,
		Host:      &host,
		Port:      &port,
	}, 0)
	if err != nil {
		t.Fatalf("UpdateProxyAndWait: %v", err)
	}
	if final.ID != server.TaskID() || final.Status != "Successful" {
		t.Fatalf("final task = %+v", final)
	}

	requests := server.Requests()
	if len(requests) != 4 {
		t.Fatalf("request log = %v, want one PATCH and three GETs", requests)
	}
	patch := requests[0]
	if patch.OperationID != "updateProxyConfiguration" || patch.Method != http.MethodPatch || patch.RawTarget != "/v1/system/proxy-configuration" {
		t.Fatalf("submit request = %v", patch)
	}
	wantBody := fmt.Sprintf(`{"isEnabled":true,"host":%q,"port":3128}`, host)
	if string(patch.Body) != wantBody {
		t.Fatalf("PATCH body = %q, want %q", patch.Body, wantBody)
	}
	if patch.ContentLength != int64(len(patch.Body)) || len(patch.TransferEncoding) != 0 {
		t.Fatalf("PATCH framing contentLength=%d transferEncoding=%v body=%d", patch.ContentLength, patch.TransferEncoding, len(patch.Body))
	}
	if values := patch.Header.Values("Content-Length"); len(values) != 0 && (len(values) != 1 || values[0] != fmt.Sprint(len(patch.Body))) {
		t.Fatalf("PATCH Content-Length values = %v, want exactly [%d] when retained by net/http", values, len(patch.Body))
	}
	assertSingleHeader(t, patch.Header, "Authorization", "Bearer "+token)
	assertSingleHeader(t, patch.Header, "Accept", "application/json")
	assertSingleHeader(t, patch.Header, "Content-Type", "application/json")
	// Go releases differ on whether the server-side Header map retains the
	// special Content-Length header. ContentLength above verifies the wire
	// framing on every supported release; allowing the map entry keeps this
	// assertion portable without weakening the contract.
	assertHeaderNames(t, patch.Header, "Accept", "Accept-Encoding", "Authorization", "Content-Length", "Content-Type", "User-Agent")
	var members map[string]json.RawMessage
	if err := json.Unmarshal(patch.Body, &members); err != nil {
		t.Fatalf("decode PATCH body: %v", err)
	}
	wantMembers := []string{"host", "isEnabled", "port"}
	gotMembers := make([]string, 0, len(members))
	for member := range members {
		gotMembers = append(gotMembers, member)
	}
	for _, forbidden := range []string{"isConfigured", "transferProtocol", "username", "password", "isAuthenticated"} {
		if _, present := members[forbidden]; present {
			t.Errorf("unset optional member %q was sent", forbidden)
		}
	}
	if len(gotMembers) != len(wantMembers) {
		t.Fatalf("PATCH members = %v, want exactly %v", gotMembers, wantMembers)
	}

	wantTarget := "/v1/tasks/" + url.PathEscape(server.TaskID())
	for index, request := range requests[1:] {
		if request.OperationID != "getTask" || request.Method != http.MethodGet || request.RawTarget != wantTarget {
			t.Fatalf("poll request %d = %v", index, request)
		}
		assertSingleHeader(t, request.Header, "Authorization", "Bearer "+token)
		assertSingleHeader(t, request.Header, "Accept", "application/json")
		assertHeaderNames(t, request.Header, "Accept", "Accept-Encoding", "Authorization", "User-Agent")
		if values := request.Header.Values("Content-Type"); len(values) != 0 {
			t.Errorf("GET %d Content-Type values = %v, want absent", index, values)
		}
		if len(request.Body) != 0 || request.ContentLength > 0 || len(request.TransferEncoding) != 0 {
			t.Errorf("GET %d framing body=%d contentLength=%d transferEncoding=%v", index, len(request.Body), request.ContentLength, request.TransferEncoding)
		}
	}
}

func TestOptionalBooleanAndOmissionTable(t *testing.T) {
	falseValue := false
	emptyString := ""
	zeroPort := int32(0)
	tests := []struct {
		name       string
		config     vcfinstaller.ProxyConfiguration
		wantBody   string
		wantFields map[string]bool
	}{
		{
			name:       "all optional fields omitted",
			config:     vcfinstaller.ProxyConfiguration{IsEnabled: false},
			wantBody:   `{"isEnabled":false}`,
			wantFields: map[string]bool{"isEnabled": true},
		},
		{
			name: "explicit false remains present",
			config: vcfinstaller.ProxyConfiguration{
				IsEnabled:       true,
				IsAuthenticated: &falseValue,
			},
			wantBody:   `{"isEnabled":true,"isAuthenticated":false}`,
			wantFields: map[string]bool{"isEnabled": true, "isAuthenticated": true},
		},
		{
			name: "all explicit zero values remain present",
			config: vcfinstaller.ProxyConfiguration{
				IsEnabled:        true,
				Host:             &emptyString,
				Port:             &zeroPort,
				TransferProtocol: &emptyString,
				Username:         &emptyString,
				Password:         &emptyString,
				IsAuthenticated:  &falseValue,
			},
			wantBody: `{"isEnabled":true,"host":"","port":0,"transferProtocol":"","username":"","password":"","isAuthenticated":false}`,
			wantFields: map[string]bool{
				"isEnabled": true, "host": true, "port": true, "transferProtocol": true,
				"username": true, "password": true, "isAuthenticated": true,
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.Start(t, filepath.Join(repositoryRoot(t), "docs", "contract.json"), []string{"Successful"})
			client, err := vcfinstaller.NewClient(server.URL(), "table-token", nil)
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			if _, err := client.UpdateProxyAndWait(context.Background(), test.config, 0); err != nil {
				t.Fatalf("UpdateProxyAndWait: %v", err)
			}
			requests := server.Requests()
			if len(requests) != 2 {
				t.Fatalf("requests = %v, want submit plus mandatory poll", requests)
			}
			if string(requests[0].Body) != test.wantBody {
				t.Fatalf("body = %q, want %q", requests[0].Body, test.wantBody)
			}
			var members map[string]any
			if err := json.Unmarshal(requests[0].Body, &members); err != nil {
				t.Fatal(err)
			}
			gotFields := make(map[string]bool, len(members))
			for name := range members {
				gotFields[name] = true
			}
			if !reflect.DeepEqual(gotFields, test.wantFields) {
				t.Fatalf("members = %v, want %v", gotFields, test.wantFields)
			}
		})
	}
}

func TestNonTerminalStatusTable(t *testing.T) {
	tests := []struct {
		name   string
		status string
	}{
		{name: "pending", status: "Pending"},
		{name: "in progress", status: " IN_PROGRESS "},
		{name: "queued", status: "Queued"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.Start(t, filepath.Join(repositoryRoot(t), "docs", "contract.json"), []string{test.status, "Successful"})
			client, err := vcfinstaller.NewClient(server.URL(), "nonterminal-token", nil)
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			task, err := client.UpdateProxyAndWait(context.Background(), vcfinstaller.ProxyConfiguration{IsEnabled: false}, 0)
			if err != nil || task.Status != "Successful" {
				t.Fatalf("task=%+v error=%v", task, err)
			}
			if got := server.Requests(); len(got) != 3 || got[1].OperationID != "getTask" || got[2].OperationID != "getTask" {
				t.Fatalf("nonterminal request log = %v", got)
			}
		})
	}
}

func TestTerminalStatusTable(t *testing.T) {
	tests := []struct {
		name         string
		status       string
		wantFailure  bool
		wantProtocol bool
	}{
		{name: "successful spelling", status: "  Successful  "},
		{name: "failed", status: "FAILED", wantFailure: true},
		{name: "cancelled", status: "Cancelled", wantFailure: true},
		{name: "warning terminal", status: "completed with warning", wantFailure: true},
		{name: "skipped", status: "SKIPPED", wantFailure: true},
		{name: "timed out", status: "Timed Out", wantFailure: true},
		{name: "unknown", status: "ALMOST_DONE", wantProtocol: true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server := contractmock.Start(t, filepath.Join(repositoryRoot(t), "docs", "contract.json"), []string{test.status})
			client, err := vcfinstaller.NewClient(server.URL(), "status-token", nil)
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			task, err := client.UpdateProxyAndWait(context.Background(), vcfinstaller.ProxyConfiguration{IsEnabled: false}, 0)
			var failed *vcfinstaller.TaskFailedError
			var protocol *vcfinstaller.ProtocolError
			if errors.As(err, &failed) != test.wantFailure || errors.As(err, &protocol) != test.wantProtocol {
				t.Fatalf("error = %T %v, want failure=%v protocol=%v", err, err, test.wantFailure, test.wantProtocol)
			}
			if !test.wantFailure && !test.wantProtocol && err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if task.ID != server.TaskID() || task.Status != test.status {
				t.Fatalf("returned task = %+v", task)
			}
			if failed != nil && (failed.Task.ID != task.ID || failed.Task.Status != task.Status) {
				t.Fatalf("TaskFailedError task = %+v, returned %+v", failed.Task, task)
			}
			if got := server.Requests(); len(got) != 2 || got[1].OperationID != "getTask" {
				t.Fatalf("mandatory poll log = %v", got)
			}
		})
	}
}

func TestNewClientValidation(t *testing.T) {
	tests := []struct {
		name, baseURL, token string
	}{
		{name: "empty URL", token: "token"},
		{name: "non HTTP scheme", baseURL: "ftp://installer.example", token: "token"},
		{name: "missing host", baseURL: "https:///v1", token: "token"},
		{name: "userinfo", baseURL: "https://user:password@installer.example", token: "token"},
		{name: "non-root path", baseURL: "https://installer.example/base", token: "token"},
		{name: "query", baseURL: "https://installer.example?debug=true", token: "token"},
		{name: "fragment", baseURL: "https://installer.example#fragment", token: "token"},
		{name: "empty token", baseURL: "https://installer.example"},
		{name: "blank token", baseURL: "https://installer.example", token: " \t "},
		{name: "carriage return", baseURL: "https://installer.example", token: "secret\rinjected"},
		{name: "line feed", baseURL: "https://installer.example", token: "secret\ninjected"},
		{name: "NUL", baseURL: "https://installer.example", token: "secret\x00injected"},
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
		t.Fatalf("valid HTTP service root and header value were rejected: client=%v error=%v", client, err)
	}
}

func TestArgumentErrorsDoNotSendRequests(t *testing.T) {
	server := contractmock.Start(t, filepath.Join(repositoryRoot(t), "docs", "contract.json"), []string{"Successful"})
	client, err := vcfinstaller.NewClient(server.URL(), "argument-token", nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := client.UpdateProxyAndWait(nil, vcfinstaller.ProxyConfiguration{}, 0); err == nil {
		t.Fatal("nil context was accepted")
	}
	if _, err := client.UpdateProxyAndWait(context.Background(), vcfinstaller.ProxyConfiguration{}, -time.Nanosecond); err == nil {
		t.Fatal("negative poll interval was accepted")
	}
	if requests := server.Requests(); len(requests) != 0 {
		t.Fatalf("invalid calls sent requests: %v", requests)
	}
}

func TestAcceptedTerminalTaskIsStillPolled(t *testing.T) {
	taskID := "terminal/accepted"
	responses := []scriptedResponse{
		{status: http.StatusAccepted, contentType: "application/json", body: taskBody(taskID, "Successful")},
		{status: http.StatusOK, contentType: "application/json; charset=utf-8", body: taskBody(taskID, "Successful")},
	}
	server, count := startScriptedServer(t, responses)
	client, err := vcfinstaller.NewClient(server.URL, "mandatory-token", nil)
	if err != nil {
		t.Fatal(err)
	}
	task, err := client.UpdateProxyAndWait(context.Background(), vcfinstaller.ProxyConfiguration{}, 0)
	if err != nil || task.ID != taskID {
		t.Fatalf("task=%+v error=%v", task, err)
	}
	if got := count.Load(); got != 2 {
		t.Fatalf("request count = %d, want accepted PATCH plus mandatory GET", got)
	}
}

func TestSuccessfulResponseValidation(t *testing.T) {
	validAccepted := scriptedResponse{status: http.StatusAccepted, contentType: "application/json", body: taskBody("task-1", "PENDING")}
	tests := []struct {
		name       string
		responses  []scriptedResponse
		operation  string
		wantAPI    bool
		wantStatus int
	}{
		{
			name: "PATCH exact status", operation: "updateProxyConfiguration", wantAPI: true, wantStatus: http.StatusOK,
			responses: []scriptedResponse{{status: http.StatusOK, contentType: "text/plain", body: `not a task`}},
		},
		{
			name: "GET exact status", operation: "getTask", wantAPI: true, wantStatus: http.StatusAccepted,
			responses: []scriptedResponse{validAccepted, {status: http.StatusAccepted, contentType: "text/plain", body: `not a task`}},
		},
		{
			name: "PATCH JSON media type", operation: "updateProxyConfiguration",
			responses: []scriptedResponse{{status: http.StatusAccepted, contentType: "text/plain", body: taskBody("task-1", "PENDING")}},
		},
		{
			name: "GET JSON media type", operation: "getTask",
			responses: []scriptedResponse{validAccepted, {status: http.StatusOK, contentType: "", body: taskBody("task-1", "Successful")}},
		},
		{
			name: "JSON Task object", operation: "updateProxyConfiguration",
			responses: []scriptedResponse{{status: http.StatusAccepted, contentType: "application/json", body: `[]`}},
		},
		{
			name: "polled ID continuity", operation: "getTask",
			responses: []scriptedResponse{validAccepted, {status: http.StatusOK, contentType: "application/json", body: taskBody("different-task", "Successful")}},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			server, _ := startScriptedServer(t, test.responses)
			client, err := vcfinstaller.NewClient(server.URL, "protocol-token", nil)
			if err != nil {
				t.Fatal(err)
			}
			_, err = client.UpdateProxyAndWait(context.Background(), vcfinstaller.ProxyConfiguration{}, 0)
			var api *vcfinstaller.APIError
			var protocol *vcfinstaller.ProtocolError
			if test.wantAPI {
				if !errors.As(err, &api) || api.OperationID != test.operation || api.StatusCode != test.wantStatus {
					t.Fatalf("error = %T %v, want APIError operation=%q status=%d", err, err, test.operation, test.wantStatus)
				}
			} else if !errors.As(err, &protocol) || protocol.OperationID != test.operation {
				t.Fatalf("error = %T %v, want ProtocolError operation=%q", err, err, test.operation)
			}
		})
	}
}

func TestRequiredTaskFields(t *testing.T) {
	fields := []string{"id", "name", "status", "creationTimestamp"}
	for _, operation := range []string{"updateProxyConfiguration", "getTask"} {
		for _, missing := range fields {
			t.Run(operation+" missing "+missing, func(t *testing.T) {
				body := map[string]string{
					"id": "task-1", "name": "Update proxy", "status": "Successful", "creationTimestamp": "2026-08-02T12:00:00Z",
				}
				body[missing] = " \t "
				encoded, err := json.Marshal(body)
				if err != nil {
					t.Fatal(err)
				}
				invalid := scriptedResponse{status: http.StatusAccepted, contentType: "application/json", body: string(encoded)}
				responses := []scriptedResponse{invalid}
				if operation == "getTask" {
					invalid.status = http.StatusOK
					responses = []scriptedResponse{
						{status: http.StatusAccepted, contentType: "application/json", body: taskBody("task-1", "PENDING")},
						invalid,
					}
				}
				server, _ := startScriptedServer(t, responses)
				client, err := vcfinstaller.NewClient(server.URL, "required-token", nil)
				if err != nil {
					t.Fatal(err)
				}
				_, err = client.UpdateProxyAndWait(context.Background(), vcfinstaller.ProxyConfiguration{}, 0)
				var protocol *vcfinstaller.ProtocolError
				if !errors.As(err, &protocol) || protocol.OperationID != operation {
					t.Fatalf("error = %T %v, want ProtocolError for %s", err, err, operation)
				}
			})
		}
	}
}

func TestContextCancellationInterruptsPollDelay(t *testing.T) {
	polled := make(chan struct{}, 1)
	var requests atomic.Int32
	transport := roundTripFunc(func(request *http.Request) (*http.Response, error) {
		index := requests.Add(1)
		status := "PENDING"
		statusCode := http.StatusAccepted
		if index == 2 {
			statusCode = http.StatusOK
			polled <- struct{}{}
		}
		return &http.Response{
			StatusCode: statusCode,
			Header:     http.Header{"Content-Type": []string{"application/json"}},
			Body:       io.NopCloser(strings.NewReader(taskBody("task-1", status))),
			Request:    request,
		}, nil
	})
	client, err := vcfinstaller.NewClient("https://installer.example", "cancel-token", &http.Client{Transport: transport})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result := make(chan error, 1)
	go func() {
		_, callErr := client.UpdateProxyAndWait(ctx, vcfinstaller.ProxyConfiguration{}, time.Hour)
		result <- callErr
	}()
	select {
	case <-polled:
	case <-time.After(2 * time.Second):
		t.Fatal("client did not perform the mandatory poll")
	}
	cancel()
	select {
	case callErr := <-result:
		if !errors.Is(callErr, context.Canceled) {
			t.Fatalf("error = %T %v, want context.Canceled", callErr, callErr)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("context cancellation did not interrupt the poll delay")
	}
}

func TestTransportErrorsDoNotExposeSecrets(t *testing.T) {
	token := "access-secret-0209"
	password := "proxy-secret-0209"
	secretError := errors.New("I/O echoed " + token + " and " + password)
	tests := []struct {
		name      string
		transport roundTripFunc
	}{
		{
			name: "transport error",
			transport: func(*http.Request) (*http.Response, error) {
				return nil, secretError
			},
		},
		{
			name: "response body error",
			transport: func(request *http.Request) (*http.Response, error) {
				return &http.Response{
					StatusCode: http.StatusAccepted,
					Header:     http.Header{"Content-Type": []string{"application/json"}},
					Body:       io.NopCloser(errorReader{err: secretError}),
					Request:    request,
				}, nil
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			client, err := vcfinstaller.NewClient("https://installer.example", token, &http.Client{Transport: test.transport})
			if err != nil {
				t.Fatal(err)
			}
			_, err = client.UpdateProxyAndWait(context.Background(), vcfinstaller.ProxyConfiguration{Password: &password}, 0)
			if err == nil {
				t.Fatal("I/O failure returned nil error")
			}
			if strings.Contains(err.Error(), token) || strings.Contains(err.Error(), password) {
				t.Fatalf("I/O error exposed a request secret: %v", err)
			}
		})
	}
}

type scriptedResponse struct {
	status      int
	contentType string
	body        string
}

func startScriptedServer(t *testing.T, responses []scriptedResponse) (*httptest.Server, *atomic.Int32) {
	t.Helper()
	var count atomic.Int32
	handler := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		index := int(count.Add(1)) - 1
		if index >= len(responses) {
			t.Errorf("unexpected request %d", index+1)
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		response := responses[index]
		if response.contentType != "" {
			w.Header().Set("Content-Type", response.contentType)
		}
		w.WriteHeader(response.status)
		_, _ = io.WriteString(w, response.body)
	})
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen on loopback: %v", err)
	}
	server := &httptest.Server{
		Listener: listener,
		Config:   &http.Server{Handler: handler},
	}
	server.Start()
	t.Cleanup(server.Close)
	return server, &count
}

func taskBody(id, status string) string {
	encoded, _ := json.Marshal(vcfinstaller.Task{
		ID: id, Name: "Update proxy", Status: status, CreationTimestamp: "2026-08-02T12:00:00Z",
	})
	return string(encoded)
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

type errorReader struct{ err error }

func (reader errorReader) Read([]byte) (int, error) { return 0, reader.err }

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
