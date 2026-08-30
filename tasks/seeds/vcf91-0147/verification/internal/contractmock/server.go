// Package contractmock provides a contract-pinned loopback service for
// protected verification. It never contacts a VMware or Kubernetes endpoint.
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

// Observation is one Cluster Available condition returned by create or get.
type Observation struct {
	Status  string
	Reason  string
	Message string
}

// Scenario is runtime-created service state.
type Scenario struct {
	Namespace         string
	Supervisor        string
	ClusterName       string
	ClusterClass      string
	KubernetesVersion string
	Observations      []Observation

	NamespaceStatus     int
	ClusterCreateStatus int
	ClusterGetStatus    int
	ErrorBody           string
}

// RequestRecord is one durable JSONL request-log entry.
type RequestRecord struct {
	ContractName  string              `json:"contract_name"`
	Operation     string              `json:"operation"`
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
	ContractName string `json:"contractName"`
	OperationID  string `json:"operationId"`
	OperationKey string `json:"operationKey"`
	Method       string `json:"method"`
	PathTemplate string `json:"pathTemplate"`
	RawQuery     string `json:"rawQuery"`
}

func (o contractOperation) identity() string {
	if o.OperationID != "" {
		return o.OperationID
	}
	return o.OperationKey
}

// Server is one ephemeral IPv4 loopback service and its durable request log.
type Server struct {
	test     testing.TB
	http     *httptest.Server
	client   *http.Client
	url      string
	logFile  *os.File
	logPath  string
	routes   []contractOperation
	scenario Scenario

	mu              sync.Mutex
	nextObservation int
	closed          bool
}

// Start loads docs/contract.json, derives the complete route allow-list, and
// starts a service on an ephemeral 127.0.0.1 port.
func Start(t testing.TB, scenario Scenario) *Server {
	t.Helper()
	routes := loadRoutes(t)
	if scenario.Namespace == "" || scenario.Supervisor == "" ||
		scenario.ClusterName == "" || scenario.ClusterClass == "" ||
		scenario.KubernetesVersion == "" {
		t.Fatal("contractmock scenario identifiers must be nonempty")
	}
	if len(scenario.Observations) == 0 {
		t.Fatal("contractmock scenario requires Cluster observations")
	}

	logFile, err := os.CreateTemp(t.TempDir(), "contract-requests-*.jsonl")
	if err != nil {
		t.Fatalf("create request log: %v", err)
	}
	scenario.Observations = append([]Observation(nil), scenario.Observations...)
	server := &Server{
		test:            t,
		logFile:         logFile,
		logPath:         logFile.Name(),
		routes:          routes,
		scenario:        scenario,
		nextObservation: 1,
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		server.url = "http://127.0.0.1:1"
		server.client = &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
			copy := request.Clone(request.Context())
			copy.RequestURI = copy.URL.RequestURI()
			recorder := httptest.NewRecorder()
			server.handle(recorder, copy)
			return recorder.Result(), nil
		})}
		t.Cleanup(server.Close)
		return server
	}
	httpServer := httptest.NewUnstartedServer(http.HandlerFunc(server.handle))
	_ = httpServer.Listener.Close()
	httpServer.Listener = listener
	httpServer.Start()
	server.http = httpServer
	server.client = httpServer.Client()
	server.url = httpServer.URL
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
	expected := map[string]struct {
		method    string
		operation string
	}{
		"createSupervisorNamespace": {http.MethodPost, "Vcenter.Namespaces.Instances_createV2"},
		"createVksCluster":          {http.MethodPost, "cluster.x-k8s.io/v1beta2:namespaced-clusters:create"},
		"getVksCluster":             {http.MethodGet, "cluster.x-k8s.io/v1beta2:namespaced-clusters:get"},
	}
	if len(document.Operations) != len(expected) {
		t.Fatalf("focused contract has %d operations, want %d", len(document.Operations), len(expected))
	}
	seen := make(map[string]bool, len(expected))
	for _, operation := range document.Operations {
		want, exists := expected[operation.ContractName]
		if !exists {
			t.Fatalf("focused contract contains unexpected operation %q", operation.ContractName)
		}
		if seen[operation.ContractName] {
			t.Fatalf("focused contract repeats operation %q", operation.ContractName)
		}
		if operation.Method != want.method || operation.identity() != want.operation ||
			operation.PathTemplate == "" || operation.RawQuery != "" {
			t.Fatalf("focused contract identity changed for %q", operation.ContractName)
		}
		seen[operation.ContractName] = true
	}
	return append([]contractOperation(nil), document.Operations...)
}

// URL returns the loopback HTTP origin.
func (s *Server) URL() string {
	return s.url
}

// HTTPClient returns a client that reaches the loopback service. In a sandbox
// that denies socket creation it uses an in-process fallback with the same
// handler and durable request log.
func (s *Server) HTTPClient() *http.Client {
	return s.client
}

// LogPath returns the durable JSONL request-log path.
func (s *Server) LogPath() string {
	return s.logPath
}

// Records synchronizes and reads the request log.
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
	var body []byte
	if request.Body != nil {
		var err error
		body, err = io.ReadAll(io.LimitReader(request.Body, maxRequestBody+1))
		if err != nil || len(body) > maxRequestBody {
			http.Error(writer, "bad request", http.StatusBadRequest)
			return
		}
	}
	operation, captures := s.match(request)
	record := RequestRecord{
		Method:        request.Method,
		RequestURI:    request.RequestURI,
		Header:        request.Header.Clone(),
		Body:          string(body),
		ContentLength: request.ContentLength,
	}
	if operation != nil {
		record.ContractName = operation.ContractName
		record.Operation = operation.identity()
	}
	if err := s.appendRecord(record); err != nil {
		http.Error(writer, "request log failure", http.StatusInternalServerError)
		return
	}
	if operation == nil {
		http.NotFound(writer, request)
		return
	}
	if namespace, exists := captures["namespace"]; exists && namespace != s.scenario.Namespace {
		http.NotFound(writer, request)
		return
	}
	if name, exists := captures["cluster_name"]; exists && name != s.scenario.ClusterName {
		http.NotFound(writer, request)
		return
	}

	switch operation.ContractName {
	case "createSupervisorNamespace":
		s.serveNamespaceCreate(writer)
	case "createVksCluster":
		s.serveClusterCreate(writer)
	case "getVksCluster":
		s.serveClusterGet(writer)
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

func (s *Server) match(request *http.Request) (*contractOperation, map[string]string) {
	for index := range s.routes {
		operation := &s.routes[index]
		if request.Method != operation.Method || request.URL.RawQuery != operation.RawQuery {
			continue
		}
		captures, ok := matchPath(operation.PathTemplate, request.URL.EscapedPath())
		if ok {
			return operation, captures
		}
	}
	return nil, nil
}

func matchPath(template, escapedPath string) (map[string]string, bool) {
	templateParts := strings.Split(template, "/")
	pathParts := strings.Split(escapedPath, "/")
	if len(templateParts) != len(pathParts) {
		return nil, false
	}
	captures := make(map[string]string)
	for index := range templateParts {
		part := templateParts[index]
		if strings.HasPrefix(part, "{") && strings.HasSuffix(part, "}") {
			if pathParts[index] == "" {
				return nil, false
			}
			value, err := url.PathUnescape(pathParts[index])
			if err != nil {
				return nil, false
			}
			captures[strings.TrimSuffix(strings.TrimPrefix(part, "{"), "}")] = value
			continue
		}
		if part != pathParts[index] {
			return nil, false
		}
	}
	return captures, true
}

func (s *Server) serveNamespaceCreate(writer http.ResponseWriter) {
	status := s.scenario.NamespaceStatus
	if status == 0 {
		status = http.StatusNoContent
	}
	if status != http.StatusNoContent {
		s.writeError(writer, status)
		return
	}
	writer.WriteHeader(status)
}

func (s *Server) serveClusterCreate(writer http.ResponseWriter) {
	status := s.scenario.ClusterCreateStatus
	if status == 0 {
		status = http.StatusCreated
	}
	if status != http.StatusCreated {
		s.writeError(writer, status)
		return
	}
	s.writeObservation(writer, status, 0)
}

func (s *Server) serveClusterGet(writer http.ResponseWriter) {
	status := s.scenario.ClusterGetStatus
	if status == 0 {
		status = http.StatusOK
	}
	if status != http.StatusOK {
		s.writeError(writer, status)
		return
	}
	s.mu.Lock()
	index := s.nextObservation
	if s.nextObservation < len(s.scenario.Observations)-1 {
		s.nextObservation++
	}
	s.mu.Unlock()
	s.writeObservation(writer, status, index)
}

func (s *Server) writeObservation(writer http.ResponseWriter, status, index int) {
	if index >= len(s.scenario.Observations) {
		index = len(s.scenario.Observations) - 1
	}
	observation := s.scenario.Observations[index]
	writeJSON(writer, status, map[string]any{
		"apiVersion": "cluster.x-k8s.io/v1beta2",
		"kind":       "Cluster",
		"metadata": map[string]any{
			"name":            s.scenario.ClusterName,
			"namespace":       s.scenario.Namespace,
			"resourceVersion": fmt.Sprintf("%d", index+1),
		},
		"spec": map[string]any{
			"topology": map[string]any{
				"class":   s.scenario.ClusterClass,
				"version": s.scenario.KubernetesVersion,
			},
		},
		"status": map[string]any{
			"conditions": []any{
				map[string]any{
					"type":    "Available",
					"status":  observation.Status,
					"reason":  observation.Reason,
					"message": observation.Message,
				},
			},
		},
	})
}

func (s *Server) writeError(writer http.ResponseWriter, status int) {
	body := s.scenario.ErrorBody
	if body == "" {
		body = "fixture API error"
	}
	writeJSON(writer, status, map[string]any{"error": body})
}

func writeJSON(writer http.ResponseWriter, status int, value any) {
	body, err := json.Marshal(value)
	if err != nil {
		http.Error(writer, "fixture encoding failure", http.StatusInternalServerError)
		return
	}
	writer.Header().Set("Content-Type", "application/json")
	writer.Header().Set("Content-Length", fmt.Sprintf("%d", len(body)))
	writer.WriteHeader(status)
	_, _ = writer.Write(body)
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}
