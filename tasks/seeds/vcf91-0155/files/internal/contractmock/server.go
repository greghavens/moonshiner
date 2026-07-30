// Package contractmock provides a loopback-only fixture whose route allow-list
// is loaded from docs/contract.json.
package contractmock

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"regexp"
	"strings"
	"sync"
	"testing"
)

// ClusterFixture is one Kubernetes Cluster projection served by the mock.
type ClusterFixture struct {
	Name            string
	TopologyVersion string
}

// Options controls response state without adding routes.
type Options struct {
	TaskStatuses    []string
	NamespaceStatus string
	AfterClusters   []ClusterFixture
	InitialReverse  bool
}

// RequestRecord is a concurrency-safe snapshot of one received request.
type RequestRecord struct {
	Operation        string
	Method           string
	RequestURI       string
	Header           http.Header
	Body             []byte
	ContentLength    int64
	TransferEncoding []string
	ResponseOrder    []string
}

type operation struct {
	ContractName string `json:"contractName"`
	OperationID  string `json:"operationId"`
	OperationKey string `json:"operationKey"`
	Method       string `json:"method"`
	PathTemplate string `json:"pathTemplate"`
}

type contractDocument struct {
	Operations []operation `json:"operations"`
}

type route struct {
	operation  operation
	expression *regexp.Regexp
	variables  []string
}

// Server is an isolated loopback HTTP fixture.
type Server struct {
	t               testing.TB
	http            *httptest.Server
	origin          string
	fallbackClient  *http.Client
	routes          []route
	mu              sync.Mutex
	log             []RequestRecord
	taskReads       int
	clusterReads    int
	taskStatuses    []string
	namespaceStatus string
	beforeClusters  []ClusterFixture
	afterClusters   []ClusterFixture
	initialReverse  bool
}

// New loads the route allow-list from contractPath and starts an ephemeral
// loopback server.
func New(t testing.TB, contractPath string, options Options) *Server {
	t.Helper()

	data, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var document contractDocument
	if err := json.Unmarshal(data, &document); err != nil {
		t.Fatalf("decode contract: %v", err)
	}

	required := map[string]string{
		"getSupervisorNamespace": "Vcenter.Namespaces.Instances_getV2",
		"listVksClusters":        "cluster.x-k8s.io/v1beta2:namespaced-clusters:list",
		"createSupervisorBackup": "Vcenter.NamespaceManagement.Supervisors.Recovery.Backup.Jobs_create",
		"getTask":                "Cis.Tasks_get",
	}
	if len(document.Operations) != len(required) {
		t.Fatalf("contract operation count = %d, want %d", len(document.Operations), len(required))
	}

	routes := make([]route, 0, len(document.Operations))
	seen := make(map[string]bool, len(document.Operations))
	for _, op := range document.Operations {
		identity := op.OperationID
		if identity == "" {
			identity = op.OperationKey
		}
		if required[op.ContractName] != identity || seen[op.ContractName] {
			t.Fatalf("unexpected contract operation %q (%q)", op.ContractName, identity)
		}
		seen[op.ContractName] = true
		compiled, variables, err := compileTemplate(op.PathTemplate)
		if err != nil {
			t.Fatalf("compile %s route: %v", op.ContractName, err)
		}
		routes = append(routes, route{operation: op, expression: compiled, variables: variables})
	}

	statuses := append([]string(nil), options.TaskStatuses...)
	if len(statuses) == 0 {
		statuses = []string{"PENDING", "RUNNING", "BLOCKED", "SUCCEEDED"}
	}
	namespaceStatus := options.NamespaceStatus
	if namespaceStatus == "" {
		namespaceStatus = "RUNNING"
	}
	before := []ClusterFixture{
		{Name: "zulu", TopologyVersion: "v1.31.2+vmware.1"},
		{Name: "Alpha", TopologyVersion: "v1.30.6+vmware.1"},
	}
	after := append([]ClusterFixture(nil), before...)
	if options.AfterClusters != nil {
		after = append([]ClusterFixture(nil), options.AfterClusters...)
	}

	server := &Server{
		t:               t,
		routes:          routes,
		taskStatuses:    statuses,
		namespaceStatus: namespaceStatus,
		beforeClusters:  before,
		afterClusters:   after,
		initialReverse:  options.InitialReverse,
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err == nil {
		host, _, splitErr := net.SplitHostPort(listener.Addr().String())
		if splitErr != nil || !net.ParseIP(host).IsLoopback() {
			_ = listener.Close()
			t.Fatalf("mock listener is not loopback: %q", listener.Addr())
		}
		server.http = &httptest.Server{
			Listener: listener,
			Config:   &http.Server{Handler: http.HandlerFunc(server.serveHTTP)},
		}
		server.http.Start()
		server.origin = server.http.URL
		t.Cleanup(server.http.Close)
	} else {
		server.origin = "http://127.0.0.1"
		server.fallbackClient = &http.Client{
			Transport: handlerTransport{handler: http.HandlerFunc(server.serveHTTP)},
		}
	}
	return server
}

// URL returns the loopback origin used for both independently authenticated
// APIs.
func (s *Server) URL() string {
	return s.origin
}

// HTTPClient returns a client that reaches the loopback server. In sandboxes
// that deny socket creation, its transport invokes the same HTTP handler in
// memory.
func (s *Server) HTTPClient() *http.Client {
	if s.fallbackClient != nil {
		client := *s.fallbackClient
		return &client
	}
	return &http.Client{}
}

// Log returns a deep copy of the request log.
func (s *Server) Log() []RequestRecord {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]RequestRecord, len(s.log))
	for i, record := range s.log {
		out[i] = record
		out[i].Header = record.Header.Clone()
		out[i].Body = append([]byte(nil), record.Body...)
		out[i].TransferEncoding = append([]string(nil), record.TransferEncoding...)
		out[i].ResponseOrder = append([]string(nil), record.ResponseOrder...)
	}
	return out
}

func compileTemplate(template string) (*regexp.Regexp, []string, error) {
	if !strings.HasPrefix(template, "/") {
		return nil, nil, fmt.Errorf("path template is not absolute")
	}
	parts := strings.Split(template, "/")
	var expression strings.Builder
	expression.WriteString("^")
	var variables []string
	for i, part := range parts {
		if i > 0 {
			expression.WriteString("/")
		}
		if strings.HasPrefix(part, "{") && strings.HasSuffix(part, "}") {
			name := strings.TrimSuffix(strings.TrimPrefix(part, "{"), "}")
			if name == "" || strings.ContainsAny(name, "{}") {
				return nil, nil, fmt.Errorf("invalid placeholder %q", part)
			}
			expression.WriteString("([^/]+)")
			variables = append(variables, name)
			continue
		}
		expression.WriteString(regexp.QuoteMeta(part))
	}
	expression.WriteString("$")
	compiled, err := regexp.Compile(expression.String())
	return compiled, variables, err
}

func (s *Server) match(r *http.Request) (operation, map[string]string, bool) {
	escapedPath := r.URL.EscapedPath()
	for _, candidate := range s.routes {
		if r.Method != candidate.operation.Method {
			continue
		}
		match := candidate.expression.FindStringSubmatch(escapedPath)
		if match == nil {
			continue
		}
		values := make(map[string]string, len(candidate.variables))
		for i, name := range candidate.variables {
			value, err := url.PathUnescape(match[i+1])
			if err != nil {
				return operation{}, nil, false
			}
			values[name] = value
		}
		return candidate.operation, values, true
	}
	return operation{}, nil, false
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	var body []byte
	if r.Body != nil {
		body, _ = io.ReadAll(io.LimitReader(r.Body, 1<<20))
	}
	op, values, matched := s.match(r)
	record := RequestRecord{
		Method:           r.Method,
		RequestURI:       r.RequestURI,
		Header:           r.Header.Clone(),
		Body:             append([]byte(nil), body...),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
	}
	if matched {
		record.Operation = op.ContractName
	}

	s.mu.Lock()
	switch op.ContractName {
	case "getSupervisorNamespace":
		s.log = append(s.log, record)
		status := s.namespaceStatus
		s.mu.Unlock()
		writeJSON(w, http.StatusOK, map[string]any{
			"supervisor":    "supervisor/blue zone",
			"config_status": status,
			"description":   "",
			"messages":      []any{},
			"stats": map[string]any{
				"cpu_used":     0,
				"memory_used":  0,
				"storage_used": 0,
			},
			"access_list":   []any{},
			"storage_specs": []any{},
		})
	case "listVksClusters":
		call := s.clusterReads
		s.clusterReads++
		clusters := s.beforeClusters
		if call > 0 {
			clusters = s.afterClusters
		}
		clusters = append([]ClusterFixture(nil), clusters...)
		reverse := (call%2 == 1) != s.initialReverse
		if reverse {
			for left, right := 0, len(clusters)-1; left < right; left, right = left+1, right-1 {
				clusters[left], clusters[right] = clusters[right], clusters[left]
			}
		}
		for _, cluster := range clusters {
			record.ResponseOrder = append(record.ResponseOrder, cluster.Name)
		}
		s.log = append(s.log, record)
		s.mu.Unlock()

		items := make([]map[string]any, 0, len(clusters))
		for _, cluster := range clusters {
			items = append(items, map[string]any{
				"apiVersion": "cluster.x-k8s.io/v1beta2",
				"kind":       "Cluster",
				"metadata": map[string]any{
					"name":      cluster.Name,
					"namespace": values["namespace"],
				},
				"spec": map[string]any{
					"topology": map[string]any{"version": cluster.TopologyVersion},
				},
			})
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"apiVersion": "cluster.x-k8s.io/v1beta2",
			"kind":       "ClusterList",
			"items":      items,
		})
	case "createSupervisorBackup":
		s.log = append(s.log, record)
		s.mu.Unlock()
		writeJSON(w, http.StatusOK, "task/ 91")
	case "getTask":
		index := s.taskReads
		s.taskReads++
		status := s.taskStatuses[len(s.taskStatuses)-1]
		if index < len(s.taskStatuses) {
			status = s.taskStatuses[index]
		}
		s.log = append(s.log, record)
		s.mu.Unlock()

		payload := map[string]any{
			"cancelable": false,
			"description": map[string]any{
				"id":              "com.vmware.supervisor.backup",
				"default_message": "Supervisor backup",
				"args":            []string{},
			},
			"operation": "create",
			"service":   "com.vmware.vcenter.namespace_management.supervisors.recovery.backup.jobs",
			"status":    status,
		}
		if status == "SUCCEEDED" {
			payload["result"] = map[string]any{"archive": "backup-91"}
		}
		if status == "FAILED" {
			payload["error"] = map[string]any{"secret": "must not escape"}
		}
		writeJSON(w, http.StatusOK, payload)
	default:
		s.log = append(s.log, record)
		s.mu.Unlock()
		http.Error(w, "route is outside the pinned contract", http.StatusNotFound)
	}
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	data, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(data)
}

type handlerTransport struct {
	handler http.Handler
}

func (t handlerTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	select {
	case <-request.Context().Done():
		return nil, request.Context().Err()
	default:
	}
	copyRequest := request.Clone(request.Context())
	copyRequest.RequestURI = request.URL.RequestURI()
	if copyRequest.Body == nil {
		copyRequest.Body = http.NoBody
	}
	recorder := httptest.NewRecorder()
	t.handler.ServeHTTP(recorder, copyRequest)
	response := recorder.Result()
	response.Request = request
	return response, nil
}
