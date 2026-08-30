package vsandp

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"strings"
	"sync"
)

const (
	createSnapshotOperationID = "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task"
	getTaskOperationID        = "Snapservice.Tasks_get"
)

//go:embed docs/contract.json
var contractJSON []byte

type mockContract struct {
	BasePath     string                  `json:"base_path"`
	Operations   []mockContractOperation `json:"operations"`
	TaskStatuses mockTaskStatuses        `json:"task_statuses"`
}

type mockContractOperation struct {
	OperationID   string               `json:"operation_id"`
	Method        string               `json:"method"`
	Path          string               `json:"path"`
	RequiredQuery map[string]string    `json:"required_query,omitempty"`
	Request       *mockContractRequest `json:"request,omitempty"`
	Success       mockContractSuccess  `json:"success"`
}

type mockContractRequest struct {
	ContentType       string   `json:"content_type"`
	Schema            string   `json:"schema"`
	Required          []string `json:"required"`
	Optional          []string `json:"optional"`
	RetentionRequired []string `json:"retention_required"`
}

type mockContractSuccess struct {
	Status      int      `json:"status"`
	ContentType string   `json:"content_type"`
	Schema      string   `json:"schema"`
	Semantic    string   `json:"semantic,omitempty"`
	Required    []string `json:"required,omitempty"`
}

type mockTaskStatuses struct {
	NonTerminal []string `json:"non_terminal"`
	Terminal    []string `json:"terminal"`
}

// MockScenario configures the two contract operations exposed by MockServer.
type MockScenario struct {
	ClusterID         string
	ProtectionGroupID string
	TaskID            string
	Statuses          []TaskStatus
}

// RequestRecord is an immutable snapshot of one request received by MockServer.
type RequestRecord struct {
	Method        string
	RequestURI    string
	Host          string
	Header        http.Header
	Body          []byte
	ContentLength int64
}

// MockServer is a loopback-only vSAN Data Protection contract fixture.
type MockServer struct {
	server   *httptest.Server
	mu       sync.Mutex
	requests []RequestRecord
	scenario MockScenario
	statuses int
	create   mockContractOperation
	getTask  mockContractOperation
}

// NewMockServer starts the contract-pinned loopback fixture.
func NewMockServer(scenario MockScenario) (*MockServer, error) {
	var contract mockContract
	decoder := json.NewDecoder(bytes.NewReader(contractJSON))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&contract); err != nil {
		return nil, fmt.Errorf("decode embedded vSAN Data Protection contract: %w", err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return nil, fmt.Errorf("embedded vSAN Data Protection contract has trailing JSON content")
	}
	want := expectedMockContract()
	if !reflect.DeepEqual(contract, want) {
		return nil, fmt.Errorf("embedded vSAN Data Protection contract has drifted")
	}
	if len(scenario.Statuses) == 0 {
		return nil, fmt.Errorf("mock task status sequence is empty")
	}

	scenario.Statuses = append([]TaskStatus(nil), scenario.Statuses...)
	mock := &MockServer{
		scenario: scenario,
		create:   contract.Operations[0],
		getTask:  contract.Operations[1],
	}
	mock.server = httptest.NewServer(http.HandlerFunc(mock.serveHTTP))
	return mock, nil
}

func expectedMockContract() mockContract {
	return mockContract{
		BasePath: "/api",
		Operations: []mockContractOperation{
			{
				OperationID:   createSnapshotOperationID,
				Method:        http.MethodPost,
				Path:          "/snapservice/clusters/{cluster}/protection-groups/{pg}/snapshots",
				RequiredQuery: map[string]string{"vmw-task": "true"},
				Request: &mockContractRequest{
					ContentType:       "application/json",
					Schema:            "Snapservice.Clusters.ProtectionGroups.Snapshots.CreateSpec",
					Required:          []string{"name"},
					Optional:          []string{"retention"},
					RetentionRequired: []string{"duration", "unit"},
				},
				Success: mockContractSuccess{
					Status:      http.StatusAccepted,
					ContentType: "application/json",
					Schema:      "string",
					Semantic:    "com.vmware.snapservice.task identifier",
				},
			},
			{
				OperationID: getTaskOperationID,
				Method:      http.MethodGet,
				Path:        "/snapservice/tasks/{task}",
				Success: mockContractSuccess{
					Status:      http.StatusOK,
					ContentType: "application/json",
					Schema:      "Snapservice.Tasks.Info",
					Required:    []string{"cancelable", "description", "operation", "service", "status"},
				},
			},
		},
		TaskStatuses: mockTaskStatuses{
			NonTerminal: []string{"PENDING", "RUNNING", "BLOCKED"},
			Terminal:    []string{"SUCCEEDED", "FAILED"},
		},
	}
}

// URL returns the mock API base URL, including /api.
func (m *MockServer) URL() string {
	if m == nil || m.server == nil {
		return ""
	}
	return m.server.URL + "/api"
}

// Client returns an HTTP client configured for this mock server.
func (m *MockServer) Client() *http.Client {
	if m == nil || m.server == nil {
		return http.DefaultClient
	}
	return m.server.Client()
}

// Close stops the mock server.
func (m *MockServer) Close() {
	if m != nil && m.server != nil {
		m.server.Close()
	}
}

// Requests returns a deep copy of the request log.
func (m *MockServer) Requests() []RequestRecord {
	m.mu.Lock()
	defer m.mu.Unlock()

	requests := make([]RequestRecord, len(m.requests))
	for i, request := range m.requests {
		requests[i] = request
		requests[i].Header = request.Header.Clone()
		requests[i].Body = append([]byte{}, request.Body...)
	}
	return requests
}

func (m *MockServer) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "cannot read request", http.StatusBadRequest)
		return
	}
	m.record(r, body)

	createPath := "/api" + strings.NewReplacer(
		"{cluster}", url.PathEscape(m.scenario.ClusterID),
		"{pg}", url.PathEscape(m.scenario.ProtectionGroupID),
	).Replace(m.create.Path)
	taskPath := "/api" + strings.ReplaceAll(m.getTask.Path, "{task}", url.PathEscape(m.scenario.TaskID))

	switch {
	case r.Method == m.create.Method && r.URL.EscapedPath() == createPath && r.URL.RawQuery == "vmw-task=true":
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(m.create.Success.Status)
		_ = json.NewEncoder(w).Encode(m.scenario.TaskID)
	case r.Method == m.getTask.Method && r.URL.EscapedPath() == taskPath && r.URL.RawQuery == "":
		status := m.nextStatus()
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(m.getTask.Success.Status)
		_ = json.NewEncoder(w).Encode(TaskInfo{
			Status:     status,
			Cancelable: status != TaskSucceeded && status != TaskFailed,
			Description: LocalizableMessage{
				ID:             "com.vmware.snapservice.snapshot.create",
				DefaultMessage: "Create protection-group snapshot",
				Args:           []string{},
			},
			Service:   "com.vmware.snapservice",
			Operation: createSnapshotOperationID,
		})
	default:
		http.NotFound(w, r)
	}
}

func (m *MockServer) record(r *http.Request, body []byte) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.requests = append(m.requests, RequestRecord{
		Method:        r.Method,
		RequestURI:    r.RequestURI,
		Host:          r.Host,
		Header:        r.Header.Clone(),
		Body:          append([]byte{}, body...),
		ContentLength: r.ContentLength,
	})
}

func (m *MockServer) nextStatus() TaskStatus {
	m.mu.Lock()
	defer m.mu.Unlock()
	index := m.statuses
	if index >= len(m.scenario.Statuses) {
		index = len(m.scenario.Statuses) - 1
	}
	status := m.scenario.Statuses[index]
	if m.statuses < len(m.scenario.Statuses) {
		m.statuses++
	}
	return status
}
