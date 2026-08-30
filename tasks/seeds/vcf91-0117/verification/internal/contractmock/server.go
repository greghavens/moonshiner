// Package contractmock provides a contract-pinned loopback vCenter service for
// protected verification. It never contacts a VMware endpoint.
package contractmock

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
)

const maxRequestBody = 1 << 20

// VM is one generated Vcenter.VM.Summary response record.
type VM struct {
	VM            string `json:"vm"`
	Name          string `json:"name"`
	PowerState    string `json:"power_state"`
	CPUCount      *int64 `json:"cpu_count,omitempty"`
	MemorySizeMiB *int64 `json:"memory_size_mib,omitempty"`
}

// Scenario is runtime-created service data. Raw bodies, when provided, replace
// generated successful responses for the corresponding operation.
type Scenario struct {
	TaskID       string
	TaskStatuses []string
	TaskResult   json.RawMessage
	VMs          []VM

	CloneStatus int
	TaskStatus  int
	ListStatus  int
	ErrorType   string
	ErrorText   string

	TaskBodies []json.RawMessage
	ListBodies []json.RawMessage
}

// RequestRecord is one durable JSONL request-log entry.
type RequestRecord struct {
	OperationID   string              `json:"operation_id"`
	Method        string              `json:"method"`
	RequestURI    string              `json:"request_uri"`
	Header        map[string][]string `json:"header"`
	Body          string              `json:"body"`
	ContentLength int64               `json:"content_length"`
}

type contractDocument struct {
	Operations []contractOperation `json:"operations"`
}

type contractOperation struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
	RawQuery    string `json:"rawQuery"`
}

// Server is one ephemeral IPv4 loopback service and its durable request log.
type Server struct {
	test     testing.TB
	http     *httptest.Server
	logFile  *os.File
	logPath  string
	routes   []contractOperation
	scenario Scenario

	mu        sync.Mutex
	taskPolls int
	listCalls int
	closed    bool
}

// Start loads docs/contract.json, derives the route allow-list, and starts a
// server on an ephemeral 127.0.0.1 port.
func Start(t testing.TB, scenario Scenario) *Server {
	t.Helper()

	routes := loadRoutes(t)
	if scenario.TaskID == "" {
		t.Fatal("contractmock scenario requires TaskID")
	}
	if len(scenario.TaskStatuses) == 0 && len(scenario.TaskBodies) == 0 {
		t.Fatal("contractmock scenario requires task statuses or raw task bodies")
	}

	logFile, err := os.CreateTemp(t.TempDir(), "contract-requests-*.jsonl")
	if err != nil {
		t.Fatalf("create contract request log: %v", err)
	}
	server := &Server{
		test:     t,
		logFile:  logFile,
		logPath:  logFile.Name(),
		routes:   routes,
		scenario: cloneScenario(scenario),
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		_ = logFile.Close()
		t.Fatalf("listen on loopback: %v", err)
	}
	httpServer := httptest.NewUnstartedServer(http.HandlerFunc(server.handle))
	_ = httpServer.Listener.Close()
	httpServer.Listener = listener
	httpServer.Start()
	server.http = httpServer
	t.Cleanup(server.Close)
	return server
}

func loadRoutes(t testing.TB) []contractOperation {
	t.Helper()
	_, currentFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("resolve contractmock source path")
	}
	contractPath := filepath.Join(filepath.Dir(currentFile), "..", "..", "docs", "contract.json")
	data, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read focused contract: %v", err)
	}
	var document contractDocument
	if err := json.Unmarshal(data, &document); err != nil {
		t.Fatalf("decode focused contract: %v", err)
	}
	expected := map[string]bool{
		"Vcenter.VM_clone$Task": false,
		"Cis.Tasks_get":         false,
		"Vcenter.VM_list":       false,
	}
	if len(document.Operations) != len(expected) {
		t.Fatalf("focused contract has %d operations, want %d", len(document.Operations), len(expected))
	}
	for _, operation := range document.Operations {
		if _, exists := expected[operation.OperationID]; !exists {
			t.Fatalf("focused contract contains unexpected operation %q", operation.OperationID)
		}
		if expected[operation.OperationID] {
			t.Fatalf("focused contract repeats operation %q", operation.OperationID)
		}
		if operation.Method == "" || operation.Path == "" {
			t.Fatalf("focused contract operation %q has incomplete route", operation.OperationID)
		}
		expected[operation.OperationID] = true
	}
	return append([]contractOperation(nil), document.Operations...)
}

func cloneScenario(input Scenario) Scenario {
	output := input
	output.TaskStatuses = append([]string(nil), input.TaskStatuses...)
	output.TaskResult = append(json.RawMessage(nil), input.TaskResult...)
	output.VMs = append([]VM(nil), input.VMs...)
	output.TaskBodies = cloneRawBodies(input.TaskBodies)
	output.ListBodies = cloneRawBodies(input.ListBodies)
	return output
}

func cloneRawBodies(input []json.RawMessage) []json.RawMessage {
	output := make([]json.RawMessage, len(input))
	for index := range input {
		output[index] = append(json.RawMessage(nil), input[index]...)
	}
	return output
}

// URL returns the HTTP origin of the loopback server.
func (s *Server) URL() string {
	return s.http.URL
}

// LogPath returns the durable JSONL request-log path.
func (s *Server) LogPath() string {
	return s.logPath
}

// Records reads and decodes the request log after synchronizing pending writes.
func (s *Server) Records() []RequestRecord {
	s.test.Helper()
	s.mu.Lock()
	if err := s.logFile.Sync(); err != nil {
		s.mu.Unlock()
		s.test.Fatalf("sync request log: %v", err)
	}
	s.mu.Unlock()

	file, err := os.Open(s.logPath)
	if err != nil {
		s.test.Fatalf("open request log: %v", err)
	}
	defer file.Close()

	var records []RequestRecord
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 4096), 2<<20)
	for scanner.Scan() {
		var record RequestRecord
		if err := json.Unmarshal(scanner.Bytes(), &record); err != nil {
			s.test.Fatalf("decode request log: %v", err)
		}
		records = append(records, record)
	}
	if err := scanner.Err(); err != nil {
		s.test.Fatalf("scan request log: %v", err)
	}
	return records
}

// Close stops the service and closes the request log.
func (s *Server) Close() {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return
	}
	s.closed = true
	s.mu.Unlock()

	if s.http != nil {
		s.http.Close()
	}
	s.mu.Lock()
	_ = s.logFile.Sync()
	_ = s.logFile.Close()
	s.mu.Unlock()
}

func (s *Server) handle(writer http.ResponseWriter, request *http.Request) {
	body, err := io.ReadAll(io.LimitReader(request.Body, maxRequestBody+1))
	if err != nil || len(body) > maxRequestBody {
		http.Error(writer, "bad request", http.StatusBadRequest)
		return
	}
	operation := s.match(request)
	record := RequestRecord{
		Method:        request.Method,
		RequestURI:    request.RequestURI,
		Header:        request.Header.Clone(),
		Body:          string(body),
		ContentLength: request.ContentLength,
	}
	if operation != nil {
		record.OperationID = operation.OperationID
	}
	if err := s.appendRecord(record); err != nil {
		http.Error(writer, "request log failure", http.StatusInternalServerError)
		return
	}
	if operation == nil {
		http.NotFound(writer, request)
		return
	}

	switch operation.OperationID {
	case "Vcenter.VM_clone$Task":
		s.serveClone(writer)
	case "Cis.Tasks_get":
		s.serveTask(writer, request)
	case "Vcenter.VM_list":
		s.serveVMs(writer)
	default:
		http.NotFound(writer, request)
	}
}

func (s *Server) appendRecord(record RequestRecord) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed {
		return fmt.Errorf("request log is closed")
	}
	encoded, err := json.Marshal(record)
	if err != nil {
		return err
	}
	if _, err := s.logFile.Write(append(encoded, '\n')); err != nil {
		return err
	}
	return s.logFile.Sync()
}

func (s *Server) match(request *http.Request) *contractOperation {
	escapedPath := request.URL.EscapedPath()
	for index := range s.routes {
		operation := &s.routes[index]
		if request.Method != operation.Method ||
			request.URL.RawQuery != operation.RawQuery ||
			!matchPath(operation.Path, escapedPath) {
			continue
		}
		return operation
	}
	return nil
}

func matchPath(template, escapedPath string) bool {
	templateParts := strings.Split(template, "/")
	pathParts := strings.Split(escapedPath, "/")
	if len(templateParts) != len(pathParts) {
		return false
	}
	for index := range templateParts {
		part := templateParts[index]
		if strings.HasPrefix(part, "{") && strings.HasSuffix(part, "}") {
			if pathParts[index] == "" {
				return false
			}
			continue
		}
		if part != pathParts[index] {
			return false
		}
	}
	return true
}

func (s *Server) serveClone(writer http.ResponseWriter) {
	status := s.scenario.CloneStatus
	if status == 0 {
		status = http.StatusAccepted
	}
	if status != http.StatusAccepted {
		s.writeAPIError(writer, status)
		return
	}
	s.mu.Lock()
	s.taskPolls = 0
	s.mu.Unlock()
	writeJSON(writer, status, s.scenario.TaskID)
}

func (s *Server) serveTask(writer http.ResponseWriter, request *http.Request) {
	rawSegment := strings.TrimPrefix(request.URL.EscapedPath(), "/api/cis/tasks/")
	taskID, err := url.PathUnescape(rawSegment)
	if err != nil || taskID != s.scenario.TaskID {
		http.NotFound(writer, request)
		return
	}
	statusCode := s.scenario.TaskStatus
	if statusCode == 0 {
		statusCode = http.StatusOK
	}
	if statusCode != http.StatusOK {
		s.writeAPIError(writer, statusCode)
		return
	}

	s.mu.Lock()
	index := s.taskPolls
	s.taskPolls++
	s.mu.Unlock()
	if len(s.scenario.TaskBodies) > 0 {
		if index >= len(s.scenario.TaskBodies) {
			index = len(s.scenario.TaskBodies) - 1
		}
		writeRawJSON(writer, statusCode, s.scenario.TaskBodies[index])
		return
	}
	if index >= len(s.scenario.TaskStatuses) {
		index = len(s.scenario.TaskStatuses) - 1
	}
	status := s.scenario.TaskStatuses[index]
	response := map[string]any{
		"description": map[string]any{
			"id":              "com.vmware.vcenter.vm.clone",
			"default_message": "Clone virtual machine",
			"args":            []string{},
		},
		"service":    "com.vmware.vcenter.vm",
		"operation":  "clone",
		"status":     status,
		"cancelable": status != "SUCCEEDED" && status != "FAILED",
	}
	if status == "SUCCEEDED" && s.scenario.TaskResult != nil {
		response["result"] = json.RawMessage(s.scenario.TaskResult)
	}
	if status == "FAILED" {
		response["error"] = map[string]any{
			"error_type": "ERROR",
			"messages": []any{
				map[string]any{
					"id":              "clone.failed",
					"default_message": s.errorText(),
					"args":            []string{},
				},
			},
		}
	}
	writeJSON(writer, statusCode, response)
}

func (s *Server) serveVMs(writer http.ResponseWriter) {
	status := s.scenario.ListStatus
	if status == 0 {
		status = http.StatusOK
	}
	if status != http.StatusOK {
		s.writeAPIError(writer, status)
		return
	}
	s.mu.Lock()
	index := s.listCalls
	s.listCalls++
	s.mu.Unlock()
	if len(s.scenario.ListBodies) > 0 {
		bodyIndex := index
		if bodyIndex >= len(s.scenario.ListBodies) {
			bodyIndex = len(s.scenario.ListBodies) - 1
		}
		writeRawJSON(writer, status, s.scenario.ListBodies[bodyIndex])
		return
	}
	vms := append([]VM(nil), s.scenario.VMs...)
	// The first response is reversed, and every subsequent response flips.
	if index%2 == 0 {
		for left, right := 0, len(vms)-1; left < right; left, right = left+1, right-1 {
			vms[left], vms[right] = vms[right], vms[left]
		}
	}
	writeJSON(writer, status, vms)
}

func (s *Server) writeAPIError(writer http.ResponseWriter, status int) {
	errorType := s.scenario.ErrorType
	if errorType == "" {
		errorType = "ERROR"
	}
	writeJSON(writer, status, map[string]any{
		"error_type": errorType,
		"messages": []any{
			map[string]any{
				"id":              "mock.failure",
				"default_message": s.errorText(),
				"args":            []string{},
			},
		},
	})
}

func (s *Server) errorText() string {
	if s.scenario.ErrorText != "" {
		return s.scenario.ErrorText
	}
	return "generated contract failure"
}

func writeJSON(writer http.ResponseWriter, status int, value any) {
	data, err := json.Marshal(value)
	if err != nil {
		http.Error(writer, "encode response", http.StatusInternalServerError)
		return
	}
	writeRawJSON(writer, status, data)
}

func writeRawJSON(writer http.ResponseWriter, status int, data []byte) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_, _ = writer.Write(data)
}
