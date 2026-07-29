package backupwait_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"strings"
	"sync"
	"testing"

	bw "vcf91-0025"
)

const (
	testToken = "fixture-token-91"
	taskID    = "92a5d2f0-5336-4fa2-b30b-12b05ea49ef8"
)

type recordedRequest struct {
	Method      string
	Path        string
	RawQuery    string
	Auth        []string
	Accept      []string
	ContentType []string
	Body        []byte
}

type pollReply struct {
	StatusCode int
	TaskStatus string
	Body       string
}

type operationSource struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

type mockPlan struct {
	PatchStatus int
	PatchBody   string
	Polls       []pollReply
}

type contractMock struct {
	mu        sync.Mutex
	plan      mockPlan
	pollIndex int
	requests  []recordedRequest
}

func newContractMock(t *testing.T, plan mockPlan) (*contractMock, *httptest.Server) {
	t.Helper()
	m := &contractMock{plan: plan}
	server := httptest.NewServer(http.HandlerFunc(m.serveHTTP))
	t.Cleanup(server.Close)
	return m, server
}

func (m *contractMock) requestLog() []recordedRequest {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]recordedRequest, len(m.requests))
	for i, request := range m.requests {
		out[i] = request
		out[i].Body = append([]byte(nil), request.Body...)
		out[i].Auth = append([]string(nil), request.Auth...)
		out[i].Accept = append([]string(nil), request.Accept...)
		out[i].ContentType = append([]string(nil), request.ContentType...)
	}
	return out
}

func (m *contractMock) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	record := recordedRequest{
		Method:      r.Method,
		Path:        r.URL.Path,
		RawQuery:    r.URL.RawQuery,
		Auth:        append([]string(nil), r.Header.Values("Authorization")...),
		Accept:      append([]string(nil), r.Header.Values("Accept")...),
		ContentType: append([]string(nil), r.Header.Values("Content-Type")...),
		Body:        append([]byte(nil), body...),
	}

	m.mu.Lock()
	m.requests = append(m.requests, record)
	plan := m.plan
	var reply pollReply
	if r.Method == http.MethodGet && r.URL.Path == "/v1/tasks/"+taskID && len(plan.Polls) > 0 {
		index := m.pollIndex
		if index >= len(plan.Polls) {
			index = len(plan.Polls) - 1
		} else {
			m.pollIndex++
		}
		reply = plan.Polls[index]
	}
	m.mu.Unlock()

	w.Header().Set("Content-Type", "application/json")
	switch {
	case r.Method == http.MethodPatch && r.URL.Path == "/v1/system/backup-configuration" && r.URL.RawQuery == "":
		status := plan.PatchStatus
		if status == 0 {
			status = http.StatusAccepted
		}
		responseBody := plan.PatchBody
		if responseBody == "" {
			responseBody = taskBody("PENDING")
		}
		w.WriteHeader(status)
		fmt.Fprint(w, responseBody)
	case r.Method == http.MethodGet && r.URL.Path == "/v1/tasks/"+taskID && r.URL.RawQuery == "":
		status := reply.StatusCode
		if status == 0 {
			status = http.StatusOK
		}
		responseBody := reply.Body
		if responseBody == "" {
			responseBody = taskBody(reply.TaskStatus)
		}
		w.WriteHeader(status)
		fmt.Fprint(w, responseBody)
	default:
		w.WriteHeader(http.StatusNotFound)
		fmt.Fprint(w, `{"errorCode":"NOT_IN_CONTRACT","message":"operation is not served by the contract mock"}`)
	}
}

func taskBody(status string) string {
	return fmt.Sprintf(`{"id":%q,"name":"Update backup configuration","type":"BACKUP_CONFIGURATION_UPDATE","status":%q,"creationTimestamp":"2026-07-28T12:00:00Z"}`, taskID, status)
}

func newClient(t *testing.T, server *httptest.Server, maxPolls int, pace func(context.Context, int) error) *bw.Client {
	t.Helper()
	client, err := bw.NewClient(bw.Config{
		BaseURL:    server.URL,
		Token:      testToken,
		HTTPClient: server.Client(),
		MaxPolls:   maxPolls,
		Pace:       pace,
	})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return client
}

func minimalSpec() bw.BackupConfigurationSpec {
	return bw.BackupConfigurationSpec{
		BackupLocations: []bw.BackupLocation{
			{
				Server:        "backup01.example.com",
				Port:          22,
				Protocol:      "SFTP",
				Username:      "vcf-backup",
				DirectoryPath: "/exports/vcf",
			},
		},
	}
}

func TestProtectedContractProvenance(t *testing.T) {
	var contract struct {
		DerivedFrom struct {
			Commit   string `json:"repository_commit_sha"`
			SpecPath string `json:"spec_path"`
			Version  string `json:"api_version"`
		} `json:"derived_from"`
		Operations []operationSource `json:"operations"`
	}
	readJSON(t, "docs/contract.json", &contract)

	var sources struct {
		Commit     string            `json:"repository_commit_sha"`
		SpecPath   string            `json:"spec_path"`
		Operations []operationSource `json:"operations"`
	}
	readJSON(t, "docs/official_sources.json", &sources)

	const wantCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	const wantPath = "specifications/sddc-manager/sddc-manager-openapi.json"
	if contract.DerivedFrom.Commit != wantCommit || sources.Commit != wantCommit {
		t.Fatalf("contract is not pinned to expected commit: contract=%q sources=%q", contract.DerivedFrom.Commit, sources.Commit)
	}
	if contract.DerivedFrom.SpecPath != wantPath || sources.SpecPath != wantPath {
		t.Fatalf("wrong OpenAPI source path: contract=%q sources=%q", contract.DerivedFrom.SpecPath, sources.SpecPath)
	}
	if contract.DerivedFrom.Version != "9.1.0.0" {
		t.Fatalf("wrong API version: %q", contract.DerivedFrom.Version)
	}
	want := []operationSource{
		{"updateBackupConfiguration", "PATCH", "/v1/system/backup-configuration"},
		{"getTask", "GET", "/v1/tasks/{id}"},
	}
	for _, got := range [][]operationSource{contract.Operations, sources.Operations} {
		if !reflect.DeepEqual(got, want) {
			t.Fatalf("operation provenance mismatch\n got: %#v\nwant: %#v", got, want)
		}
	}
}

func readJSON(t *testing.T, path string, out any) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	if err := json.Unmarshal(data, out); err != nil {
		t.Fatalf("decode %s: %v", path, err)
	}
}

func TestUpdateBackupConfigurationExactWireAndPolling(t *testing.T) {
	mock, server := newContractMock(t, mockPlan{Polls: []pollReply{
		{TaskStatus: "IN_PROGRESS"},
		{TaskStatus: "SUCCESSFUL"},
	}})
	var paceMu sync.Mutex
	var paceCalls []int
	client := newClient(t, server, 4, func(ctx context.Context, completedPolls int) error {
		paceMu.Lock()
		paceCalls = append(paceCalls, completedPolls)
		paceMu.Unlock()
		return ctx.Err()
	})

	task, err := client.UpdateBackupConfiguration(context.Background(), minimalSpec())
	if err != nil {
		t.Fatalf("UpdateBackupConfiguration: %v", err)
	}
	if task.ID != taskID || task.Status != "SUCCESSFUL" {
		t.Fatalf("unexpected terminal task: %+v", task)
	}

	requests := mock.requestLog()
	if len(requests) != 3 {
		t.Fatalf("expected PATCH plus two task GETs, got %d requests: %#v", len(requests), requests)
	}
	wantMethods := []string{"PATCH", "GET", "GET"}
	wantPaths := []string{"/v1/system/backup-configuration", "/v1/tasks/" + taskID, "/v1/tasks/" + taskID}
	for i, request := range requests {
		if request.Method != wantMethods[i] || request.Path != wantPaths[i] || request.RawQuery != "" {
			t.Fatalf("request %d target mismatch: %+v", i, request)
		}
		if !reflect.DeepEqual(request.Auth, []string{"Bearer " + testToken}) {
			t.Fatalf("request %d Authorization values = %#v", i, request.Auth)
		}
		if !reflect.DeepEqual(request.Accept, []string{"application/json"}) {
			t.Fatalf("request %d Accept values = %#v", i, request.Accept)
		}
	}
	if !reflect.DeepEqual(requests[0].ContentType, []string{"application/json"}) {
		t.Fatalf("PATCH Content-Type values = %#v", requests[0].ContentType)
	}
	for i := 1; i < len(requests); i++ {
		if len(requests[i].ContentType) != 0 || len(requests[i].Body) != 0 {
			t.Fatalf("GET %d unexpectedly carried entity metadata/body: %+v", i, requests[i])
		}
	}

	var gotBody any
	if err := json.Unmarshal(requests[0].Body, &gotBody); err != nil {
		t.Fatalf("PATCH body is not JSON: %v", err)
	}
	wantBody := map[string]any{
		"backupLocations": []any{
			map[string]any{
				"server":        "backup01.example.com",
				"port":          float64(22),
				"protocol":      "SFTP",
				"username":      "vcf-backup",
				"directoryPath": "/exports/vcf",
			},
		},
	}
	if !reflect.DeepEqual(gotBody, wantBody) {
		t.Fatalf("PATCH body mismatch (unset optionals must be absent)\n got: %#v\nwant: %#v", gotBody, wantBody)
	}
	if strings.Contains(string(requests[0].Body), "encryption") ||
		strings.Contains(string(requests[0].Body), "backupSchedules") ||
		strings.Contains(string(requests[0].Body), "password") ||
		strings.Contains(string(requests[0].Body), "sshFingerprint") ||
		strings.Contains(string(requests[0].Body), "null") {
		t.Fatalf("PATCH emitted an unset optional property: %s", requests[0].Body)
	}

	paceMu.Lock()
	gotPace := append([]int(nil), paceCalls...)
	paceMu.Unlock()
	if !reflect.DeepEqual(gotPace, []int{1}) {
		t.Fatalf("Pace calls = %v, want [1] (only between GET polls)", gotPace)
	}
}

func TestTerminalStatusClassification(t *testing.T) {
	tests := []struct {
		status       string
		wantTerminal bool
	}{
		{status: "SUCCESSFUL"},
		{status: "COMPLETED_WITH_WARNING"},
		{status: "FAILED", wantTerminal: true},
		{status: "CANCELLED", wantTerminal: true},
		{status: "SKIPPED", wantTerminal: true},
		{status: "TIMED_OUT", wantTerminal: true},
	}
	for _, tt := range tests {
		t.Run(tt.status, func(t *testing.T) {
			mock, server := newContractMock(t, mockPlan{Polls: []pollReply{{TaskStatus: tt.status}}})
			client := newClient(t, server, 2, nil)
			task, err := client.UpdateBackupConfiguration(context.Background(), minimalSpec())
			if task.Status != tt.status {
				t.Fatalf("returned status = %q, want %q", task.Status, tt.status)
			}
			var terminalErr *bw.TaskTerminalError
			if tt.wantTerminal {
				if !errors.As(err, &terminalErr) {
					t.Fatalf("error = %T %v, want *TaskTerminalError", err, err)
				}
				if terminalErr.Task.ID != taskID || terminalErr.Task.Status != tt.status {
					t.Fatalf("terminal error lost task: %+v", terminalErr.Task)
				}
			} else if err != nil {
				t.Fatalf("unexpected successful-terminal error: %v", err)
			}
			if got := len(mock.requestLog()); got != 2 {
				t.Fatalf("must poll accepted Task once before returning terminal result; requests=%d", got)
			}
		})
	}
}

func TestPollFailuresAreBoundedAndStop(t *testing.T) {
	tests := []struct {
		name       string
		polls      []pollReply
		maxPolls   int
		wantPolls  int
		wantStatus string
		check      func(t *testing.T, err error)
	}{
		{
			name:       "limit",
			polls:      []pollReply{{TaskStatus: "PENDING"}, {TaskStatus: "IN_PROGRESS"}},
			maxPolls:   2,
			wantPolls:  2,
			wantStatus: "IN_PROGRESS",
			check: func(t *testing.T, err error) {
				var limit *bw.PollLimitError
				if !errors.As(err, &limit) {
					t.Fatalf("error = %T %v, want *PollLimitError", err, err)
				}
				if limit.TaskID != taskID || limit.MaxPolls != 2 || limit.LastStatus != "IN_PROGRESS" {
					t.Fatalf("poll limit details lost: %+v", limit)
				}
			},
		},
		{
			name:       "unknown status",
			polls:      []pollReply{{TaskStatus: "PAUSED_BY_OPERATOR"}},
			maxPolls:   3,
			wantPolls:  1,
			wantStatus: "PAUSED_BY_OPERATOR",
			check: func(t *testing.T, err error) {
				if err == nil || !strings.Contains(err.Error(), "PAUSED_BY_OPERATOR") {
					t.Fatalf("unknown-status error must name status, got %v", err)
				}
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			mock, server := newContractMock(t, mockPlan{Polls: tt.polls})
			client := newClient(t, server, tt.maxPolls, nil)
			task, err := client.UpdateBackupConfiguration(context.Background(), minimalSpec())
			tt.check(t, err)
			if task.Status != tt.wantStatus {
				t.Fatalf("returned last task status = %q, want %q", task.Status, tt.wantStatus)
			}
			if got := len(mock.requestLog()) - 1; got != tt.wantPolls {
				t.Fatalf("GET poll count = %d, want %d", got, tt.wantPolls)
			}
		})
	}
}

func TestHTTPFailuresDecodeVCFEnvelope(t *testing.T) {
	errorBody := `{"errorCode":"VCF_BACKUP_LOCATION_INVALID","message":"backup location was rejected","remediationMessage":"verify the SFTP path","referenceToken":"ref-4021"}`
	tests := []struct {
		name       string
		plan       mockPlan
		wantStatus int
		wantCount  int
	}{
		{
			name:       "patch",
			plan:       mockPlan{PatchStatus: http.StatusBadRequest, PatchBody: errorBody},
			wantStatus: http.StatusBadRequest,
			wantCount:  1,
		},
		{
			name: "poll",
			plan: mockPlan{Polls: []pollReply{{
				StatusCode: http.StatusInternalServerError,
				Body:       errorBody,
			}}},
			wantStatus: http.StatusInternalServerError,
			wantCount:  2,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			mock, server := newContractMock(t, tt.plan)
			client := newClient(t, server, 3, nil)
			_, err := client.UpdateBackupConfiguration(context.Background(), minimalSpec())
			var apiErr *bw.APIError
			if !errors.As(err, &apiErr) {
				t.Fatalf("error = %T %v, want *APIError", err, err)
			}
			if apiErr.StatusCode != tt.wantStatus ||
				apiErr.ErrorCode != "VCF_BACKUP_LOCATION_INVALID" ||
				apiErr.Message != "backup location was rejected" ||
				apiErr.RemediationMessage != "verify the SFTP path" ||
				apiErr.ReferenceToken != "ref-4021" {
				t.Fatalf("API error envelope not preserved: %+v", apiErr)
			}
			if strings.Contains(err.Error(), testToken) {
				t.Fatalf("error leaked bearer token: %v", err)
			}
			if got := len(mock.requestLog()); got != tt.wantCount {
				t.Fatalf("request count = %d, want %d", got, tt.wantCount)
			}
		})
	}
}

func TestPaceErrorAndContextStopFurtherPolling(t *testing.T) {
	stopErr := errors.New("pacing stopped")
	tests := []struct {
		name    string
		ctx     func() context.Context
		paceErr error
		wantErr error
	}{
		{
			name:    "pace error",
			ctx:     context.Background,
			paceErr: stopErr,
			wantErr: stopErr,
		},
		{
			name: "context cancelled before pace",
			ctx: func() context.Context {
				ctx, cancel := context.WithCancel(context.Background())
				cancel()
				return ctx
			},
			wantErr: context.Canceled,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			mock, server := newContractMock(t, mockPlan{Polls: []pollReply{
				{TaskStatus: "IN_PROGRESS"},
				{TaskStatus: "SUCCESSFUL"},
			}})
			client := newClient(t, server, 3, func(ctx context.Context, _ int) error {
				if err := ctx.Err(); err != nil {
					return err
				}
				return tt.paceErr
			})
			_, err := client.UpdateBackupConfiguration(tt.ctx(), minimalSpec())
			if !errors.Is(err, tt.wantErr) {
				t.Fatalf("error = %v, want errors.Is(..., %v)", err, tt.wantErr)
			}
			if got := len(mock.requestLog()); got > 2 {
				t.Fatalf("requests continued after stop condition: %d", got)
			}
		})
	}
}

func TestNewClientValidation(t *testing.T) {
	tests := []struct {
		name   string
		config bw.Config
	}{
		{name: "empty base URL", config: bw.Config{Token: testToken, MaxPolls: 1}},
		{name: "relative base URL", config: bw.Config{BaseURL: "/relative", Token: testToken, MaxPolls: 1}},
		{name: "empty token", config: bw.Config{BaseURL: "http://127.0.0.1", MaxPolls: 1}},
		{name: "zero poll bound", config: bw.Config{BaseURL: "http://127.0.0.1", Token: testToken}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if _, err := bw.NewClient(tt.config); err == nil {
				t.Fatal("NewClient unexpectedly accepted invalid config")
			}
		})
	}
}
