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
	"strconv"
	"strings"
	"sync"
	"testing"
)

const maxRequestBody = 1 << 20

// Scenario is runtime-created service state.
type Scenario struct {
	Namespace  string
	Supervisor string
	Cluster    string
	UID        string

	NamespaceConfigStatus string
	NamespaceHTTPStatus   int
	ApplyHTTPStatus       int
	ApplyResponse         string
	ErrorBody             string

	// DropApplyCount closes this many leading PATCH connections without a
	// response. CommitDroppedApply selects whether those requests take effect.
	DropApplyCount     int
	CommitDroppedApply bool
}

// RequestRecord is one durable JSONL request-log entry.
type RequestRecord struct {
	ContractName  string      `json:"contract_name"`
	Operation     string      `json:"operation"`
	Method        string      `json:"method"`
	RequestURI    string      `json:"request_uri"`
	Header        http.Header `json:"header"`
	Body          string      `json:"body"`
	ContentLength int64       `json:"content_length"`
}

type contractDocument struct {
	Source     contractSource      `json:"source"`
	Operations []contractOperation `json:"operations"`
}

type contractSource struct {
	RepositoryCommitSHA string `json:"repositoryCommitSha"`
	SpecPath            string `json:"specPath"`
	SpecBlobSHA         string `json:"specBlobSha"`
	APIVersion          string `json:"apiVersion"`
}

type contractOperation struct {
	ContractName     string   `json:"contractName"`
	SourceKind       string   `json:"sourceKind"`
	OperationID      string   `json:"operationId"`
	OperationKey     string   `json:"operationKey"`
	Method           string   `json:"method"`
	PathTemplate     string   `json:"pathTemplate"`
	RawQuery         string   `json:"rawQuery"`
	RequiredQuery    []string `json:"requiredQuery"`
	OptionalQuery    []string `json:"optionalQuery"`
	RequestBody      bool     `json:"requestBody"`
	RequestMediaType string   `json:"requestContentType"`
	SuccessStatus    int      `json:"successStatus"`
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
	routes   map[string]contractOperation
	scenario Scenario

	mu            sync.Mutex
	closed        bool
	applyAttempts int
	effectCount   int
	appliedBody   string
}

// Start loads docs/contract.json, derives the complete route allow-list, and
// starts a service on an ephemeral 127.0.0.1 port.
func Start(t testing.TB, scenario Scenario) *Server {
	t.Helper()
	routes := loadRoutes(t)
	if scenario.Namespace == "" || scenario.Supervisor == "" ||
		scenario.Cluster == "" || scenario.UID == "" {
		t.Fatal("contractmock scenario identifiers must be nonempty")
	}
	if scenario.NamespaceConfigStatus == "" {
		scenario.NamespaceConfigStatus = "RUNNING"
	}
	if scenario.NamespaceHTTPStatus == 0 {
		scenario.NamespaceHTTPStatus = http.StatusOK
	}
	if scenario.ApplyHTTPStatus == 0 {
		scenario.ApplyHTTPStatus = http.StatusOK
	}
	if scenario.DropApplyCount < 0 {
		t.Fatal("DropApplyCount must be nonnegative")
	}

	logFile, err := os.CreateTemp(t.TempDir(), "contract-requests-*.jsonl")
	if err != nil {
		t.Fatalf("create request log: %v", err)
	}
	server := &Server{
		test:     t,
		logFile:  logFile,
		logPath:  logFile.Name(),
		routes:   routes,
		scenario: scenario,
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		server.url = "http://127.0.0.1:1"
		server.client = &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
			copy := request.Clone(request.Context())
			copy.RequestURI = copy.URL.RequestURI()
			recorder := &inProcessRecorder{ResponseRecorder: httptest.NewRecorder()}
			server.handle(recorder, copy)
			if recorder.dropped {
				return nil, io.ErrUnexpectedEOF
			}
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

func loadRoutes(t testing.TB) map[string]contractOperation {
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
	if document.Source.RepositoryCommitSHA != "3949fc33339fc5ea1b77eadb258f1cf49aa88e26" ||
		document.Source.SpecPath != "specifications/vsphere/openapi/automation/vcenter.yaml" ||
		document.Source.SpecBlobSHA != "8028b0824c4ff3503d05f44814f967938a795c40" ||
		document.Source.APIVersion != "9.1.0.0" {
		t.Fatal("focused contract source pin changed")
	}
	expected := map[string]struct {
		source       string
		method       string
		operation    string
		path         string
		success      int
		requestMedia string
	}{
		"getSupervisorNamespace": {
			"openapi",
			http.MethodGet,
			"Vcenter.Namespaces.Instances_getV2",
			"/api/vcenter/namespaces/instances/v2/{namespace}",
			http.StatusOK,
			"",
		},
		"applyVksCluster": {
			"kubernetes-resource",
			http.MethodPatch,
			"cluster.x-k8s.io/v1beta2:namespaced-clusters:server-side-apply",
			"/apis/cluster.x-k8s.io/v1beta2/namespaces/{namespace}/clusters/{cluster}",
			http.StatusOK,
			"application/apply-patch+yaml",
		},
	}
	if len(document.Operations) != len(expected) {
		t.Fatalf("focused contract has %d operations, want %d", len(document.Operations), len(expected))
	}
	routes := make(map[string]contractOperation, len(expected))
	for _, operation := range document.Operations {
		want, exists := expected[operation.ContractName]
		if !exists {
			t.Fatalf("focused contract contains unexpected operation %q", operation.ContractName)
		}
		if _, duplicate := routes[operation.ContractName]; duplicate {
			t.Fatalf("focused contract repeats operation %q", operation.ContractName)
		}
		if operation.SourceKind != want.source ||
			operation.Method != want.method ||
			operation.identity() != want.operation ||
			operation.PathTemplate != want.path ||
			operation.SuccessStatus != want.success ||
			operation.RequestMediaType != want.requestMedia {
			t.Fatalf("focused contract identity changed for %q", operation.ContractName)
		}
		if operation.ContractName == "getSupervisorNamespace" {
			if operation.RawQuery != "" || operation.RequestBody {
				t.Fatal("vCenter namespace wire projection changed")
			}
		} else if strings.Join(operation.RequiredQuery, ",") != "fieldManager" ||
			strings.Join(operation.OptionalQuery, ",") != "dryRun,fieldValidation,force,pretty" {
			t.Fatal("Kubernetes apply query projection changed")
		}
		routes[operation.ContractName] = operation
	}
	return routes
}

// URL returns the loopback HTTP origin.
func (s *Server) URL() string {
	return s.url
}

// HTTPClient returns a client that reaches the loopback service.
func (s *Server) HTTPClient() *http.Client {
	return s.client
}

// LogPath returns the durable JSONL request-log path.
func (s *Server) LogPath() string {
	return s.logPath
}

// EffectCount reports how many distinct desired apply bodies took effect.
func (s *Server) EffectCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.effectCount
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
			http.Error(writer, "request body rejected", http.StatusBadRequest)
			return
		}
	}

	contractName, operation := s.match(request)
	s.record(RequestRecord{
		ContractName:  contractName,
		Operation:     operation.identity(),
		Method:        request.Method,
		RequestURI:    request.RequestURI,
		Header:        request.Header.Clone(),
		Body:          string(body),
		ContentLength: request.ContentLength,
	})
	if contractName == "" {
		http.Error(writer, "operation not in focused contract", http.StatusNotFound)
		return
	}

	switch contractName {
	case "getSupervisorNamespace":
		s.handleNamespace(writer)
	case "applyVksCluster":
		s.handleApply(writer, body)
	default:
		http.Error(writer, "operation not in focused contract", http.StatusNotFound)
	}
}

func (s *Server) match(request *http.Request) (string, contractOperation) {
	namespacePath := "/api/vcenter/namespaces/instances/v2/" + url.PathEscape(s.scenario.Namespace)
	clusterPath := "/apis/cluster.x-k8s.io/v1beta2/namespaces/" +
		url.PathEscape(s.scenario.Namespace) + "/clusters/" + url.PathEscape(s.scenario.Cluster)
	escapedPath := request.URL.EscapedPath()
	if request.Method == http.MethodGet && escapedPath == namespacePath && request.URL.RawQuery == "" {
		return "getSupervisorNamespace", s.routes["getSupervisorNamespace"]
	}
	if request.Method == http.MethodPatch && escapedPath == clusterPath {
		return "applyVksCluster", s.routes["applyVksCluster"]
	}
	return "", contractOperation{}
}

func (s *Server) record(record RequestRecord) {
	s.mu.Lock()
	defer s.mu.Unlock()
	encoded, err := json.Marshal(record)
	if err != nil {
		s.test.Errorf("encode request record: %v", err)
		return
	}
	if _, err := s.logFile.Write(append(encoded, '\n')); err != nil {
		s.test.Errorf("write request record: %v", err)
		return
	}
	if err := s.logFile.Sync(); err != nil {
		s.test.Errorf("sync request record: %v", err)
	}
}

func (s *Server) handleNamespace(writer http.ResponseWriter) {
	status := s.scenario.NamespaceHTTPStatus
	if status != http.StatusOK {
		if status >= 300 && status < 400 {
			writer.Header().Set("Location", "/outside-focused-contract")
		}
		s.writeFailure(writer, status)
		return
	}
	writer.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(writer).Encode(map[string]any{
		"supervisor":    s.scenario.Supervisor,
		"config_status": s.scenario.NamespaceConfigStatus,
		"description":   "",
		"messages":      []any{},
		"stats": map[string]int64{
			"cpu_used":     0,
			"memory_used":  0,
			"storage_used": 0,
		},
		"access_list":   []any{},
		"storage_specs": []any{},
	})
}

func (s *Server) handleApply(writer http.ResponseWriter, body []byte) {
	s.mu.Lock()
	s.applyAttempts++
	attempt := s.applyAttempts
	shouldDrop := attempt <= s.scenario.DropApplyCount
	if !shouldDrop || s.scenario.CommitDroppedApply {
		s.applyLocked(string(body))
	}
	s.mu.Unlock()

	if shouldDrop {
		if dropper, ok := writer.(interface{ dropConnection() }); ok {
			dropper.dropConnection()
			return
		}
		hijacker, ok := writer.(http.Hijacker)
		if !ok {
			panic("loopback response writer does not support hijacking")
		}
		connection, _, err := hijacker.Hijack()
		if err != nil {
			panic(fmt.Sprintf("hijack loopback connection: %v", err))
		}
		_ = connection.Close()
		return
	}

	if s.scenario.ApplyHTTPStatus != http.StatusOK {
		s.writeFailure(writer, s.scenario.ApplyHTTPStatus)
		return
	}
	writer.Header().Set("Content-Type", "application/json")
	if s.scenario.ApplyResponse != "" {
		_, _ = io.WriteString(writer, s.scenario.ApplyResponse)
		return
	}
	s.mu.Lock()
	generation := int64(s.effectCount)
	resourceVersion := strconv.Itoa(s.effectCount)
	s.mu.Unlock()
	_ = json.NewEncoder(writer).Encode(map[string]any{
		"apiVersion": "cluster.x-k8s.io/v1beta2",
		"kind":       "Cluster",
		"metadata": map[string]any{
			"name":            s.scenario.Cluster,
			"namespace":       s.scenario.Namespace,
			"uid":             s.scenario.UID,
			"resourceVersion": resourceVersion,
			"generation":      generation,
		},
	})
}

func (s *Server) applyLocked(body string) {
	if s.appliedBody == body {
		return
	}
	s.appliedBody = body
	s.effectCount++
}

func (s *Server) writeFailure(writer http.ResponseWriter, status int) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	body := s.scenario.ErrorBody
	if body == "" {
		body = `{"error":"fixture failure"}`
	}
	_, _ = io.WriteString(writer, body)
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

type inProcessRecorder struct {
	*httptest.ResponseRecorder
	dropped bool
}

func (r *inProcessRecorder) dropConnection() {
	r.dropped = true
}
