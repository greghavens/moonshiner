// Package contractmock provides a loopback-only NSX Policy service whose
// complete route allow-list is loaded from the protected focused contract.
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
	"strings"
	"sync"
	"testing"
)

const (
	TagBulkUpdate             = "TagBulkUpdate"
	GetTagBulkOperationStatus = "GetTagBulkOperationStatus"
)

type Contract struct {
	Source struct {
		Repository          string `json:"repository"`
		RepositoryCommitSHA string `json:"repository_commit_sha"`
		SpecPath            string `json:"spec_path"`
		SpecBlobSHA         string `json:"spec_blob_sha"`
		License             string `json:"license"`
	} `json:"source"`
	Swagger             string `json:"swagger"`
	Info                Info   `json:"info"`
	BasePath            string `json:"basePath"`
	SecurityDefinitions map[string]struct {
		Type string `json:"type"`
	} `json:"securityDefinitions"`
	Operations  []Operation `json:"operations"`
	PollingRule struct {
		SubmissionOperationID string   `json:"submission_operationId"`
		PollOperationID       string   `json:"poll_operationId"`
		NonterminalStatuses   []string `json:"nonterminal_statuses"`
		SuccessfulStatuses    []string `json:"successful_terminal_statuses"`
		FailedStatuses        []string `json:"failed_terminal_statuses"`
		AcceptedIsTerminal    bool     `json:"accepted_response_is_terminal"`
		MinimumStatusPolls    int      `json:"minimum_status_polls"`
	} `json:"polling_rule"`
}

type Info struct {
	Title   string `json:"title"`
	Version string `json:"version"`
}

type Operation struct {
	OperationID string              `json:"operationId"`
	Method      string              `json:"method"`
	Path        string              `json:"path"`
	Consumes    []string            `json:"consumes"`
	Produces    []string            `json:"produces"`
	Parameters  []ContractParameter `json:"parameters"`
	Responses   map[string]any      `json:"responses"`
}

type ContractParameter struct {
	Name      string `json:"name"`
	In        string `json:"in"`
	Required  bool   `json:"required"`
	Type      string `json:"type"`
	SchemaRef string `json:"schema_ref"`
}

func LoadContract(path string) (Contract, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Contract{}, err
	}
	var contract Contract
	if err := json.Unmarshal(data, &contract); err != nil {
		return Contract{}, err
	}
	if contract.Swagger != "2.0" || contract.Info.Title == "" ||
		contract.Info.Version == "" || contract.BasePath == "" {
		return Contract{}, fmt.Errorf("contract is not a usable OpenAPI 2.0 projection")
	}
	if len(contract.Operations) == 0 {
		return Contract{}, fmt.Errorf("contract names no operations")
	}
	ids := make(map[string]bool)
	routes := make(map[string]bool)
	for _, operation := range contract.Operations {
		if operation.OperationID == "" || operation.Method == "" || operation.Path == "" {
			return Contract{}, fmt.Errorf("contract contains an incomplete operation")
		}
		route := strings.ToUpper(operation.Method) + " " + contract.BasePath + operation.Path
		if ids[operation.OperationID] || routes[route] {
			return Contract{}, fmt.Errorf("contract contains a duplicate operation or route")
		}
		ids[operation.OperationID] = true
		routes[route] = true
	}
	return contract, nil
}

// Script configures deterministic responses for one bulk operation workflow.
type Script struct {
	Statuses          []string
	PutHTTPStatus     int
	PollHTTPStatus    int
	TerminalErrorInfo bool
}

// Request is an owned request-log snapshot.
type Request struct {
	OperationID string
	Method      string
	RequestURI  string
	EscapedPath string
	RawQuery    string
	Header      http.Header
	Body        []byte
}

type Server struct {
	URL string

	contract Contract
	script   Script
	server   *httptest.Server
	client   *http.Client

	mu        sync.Mutex
	requests  []Request
	pollCount int
	tags      map[string]map[string]any
}

func New(t testing.TB, contractPath string, script Script) *Server {
	t.Helper()
	contract, err := LoadContract(contractPath)
	if err != nil {
		t.Fatalf("load contract mock: %v", err)
	}
	if len(script.Statuses) == 0 {
		script.Statuses = []string{"Success"}
	}
	mock := &Server{
		contract: contract,
		script:   script,
		tags:     make(map[string]map[string]any),
	}
	handler := http.HandlerFunc(mock.serveHTTP)
	listener, listenErr := net.Listen("tcp4", "127.0.0.1:0")
	if listenErr == nil {
		mock.server = httptest.NewUnstartedServer(handler)
		mock.server.Listener = listener
		mock.server.Start()
		mock.URL = mock.server.URL
		mock.client = mock.server.Client()
	} else {
		mock.URL = "http://127.0.0.1"
		mock.client = &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, request)
			response := recorder.Result()
			response.Request = request
			return response, nil
		})}
	}
	t.Cleanup(mock.Close)
	return mock
}

func (s *Server) Close() {
	if s.server != nil {
		s.server.Close()
	}
}

func (s *Server) Client() *http.Client {
	return s.client
}

func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	requests := make([]Request, len(s.requests))
	for i, request := range s.requests {
		requests[i] = request
		requests[i].Header = request.Header.Clone()
		requests[i].Body = append([]byte(nil), request.Body...)
	}
	return requests
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	var body []byte
	if r.Body != nil {
		body, _ = io.ReadAll(io.LimitReader(r.Body, 1<<20))
	}
	escapedPath := r.URL.EscapedPath()
	requestURI := r.RequestURI
	if requestURI == "" {
		requestURI = escapedPath
		if r.URL.ForceQuery || r.URL.RawQuery != "" {
			requestURI += "?" + r.URL.RawQuery
		}
	}
	operationID, resourceID, pathKnown := s.resolve(r.Method, escapedPath)

	s.mu.Lock()
	s.requests = append(s.requests, Request{
		OperationID: operationID,
		Method:      r.Method,
		RequestURI:  requestURI,
		EscapedPath: escapedPath,
		RawQuery:    r.URL.RawQuery,
		Header:      r.Header.Clone(),
		Body:        append([]byte(nil), body...),
	})
	s.mu.Unlock()

	if operationID == "" {
		if pathKnown {
			w.WriteHeader(http.StatusMethodNotAllowed)
		} else {
			http.NotFound(w, r)
		}
		return
	}
	if r.URL.RawQuery != "" || r.URL.ForceQuery {
		writeAPIError(w, http.StatusBadRequest)
		return
	}

	switch operationID {
	case TagBulkUpdate:
		s.handlePut(w, resourceID, body)
	case GetTagBulkOperationStatus:
		s.handleStatus(w, resourceID)
	default:
		http.Error(w, "named operation has no fixture", http.StatusNotImplemented)
	}
}

func (s *Server) handlePut(w http.ResponseWriter, operationID string, body []byte) {
	if s.script.PutHTTPStatus != 0 && s.script.PutHTTPStatus != http.StatusOK {
		writeAPIError(w, s.script.PutHTTPStatus)
		return
	}
	var request map[string]any
	if err := json.Unmarshal(body, &request); err != nil {
		writeAPIError(w, http.StatusBadRequest)
		return
	}
	tag, ok := request["tag"].(map[string]any)
	if !ok {
		writeAPIError(w, http.StatusBadRequest)
		return
	}
	s.mu.Lock()
	s.tags[operationID] = cloneMap(tag)
	s.mu.Unlock()
	writeJSON(w, http.StatusOK, map[string]any{
		"id":            operationID,
		"path":          "/infra/tags/tag-operations/" + operationID,
		"resource_type": "TagBulkOperation",
		"tag":           tag,
	})
}

func (s *Server) handleStatus(w http.ResponseWriter, operationID string) {
	if s.script.PollHTTPStatus != 0 && s.script.PollHTTPStatus != http.StatusOK {
		writeAPIError(w, s.script.PollHTTPStatus)
		return
	}
	s.mu.Lock()
	tag := cloneMap(s.tags[operationID])
	index := s.pollCount
	s.pollCount++
	if index >= len(s.script.Statuses) {
		index = len(s.script.Statuses) - 1
	}
	status := s.script.Statuses[index]
	s.mu.Unlock()
	if tag == nil {
		writeAPIError(w, http.StatusNotFound)
		return
	}
	response := map[string]any{
		"path":   "/infra/tags/tag-operations/" + operationID,
		"status": status,
		"tag":    tag,
	}
	if status == "Error" && s.script.TerminalErrorInfo {
		response["remove_from"] = []any{map[string]any{
			"resource_type": "VirtualMachine",
			"resource_tag_status": []any{map[string]any{
				"resource_id": "vm-missing",
				"tag_status":  "Error",
				"details":     "resource was not found",
			}},
		}}
	}
	writeJSON(w, http.StatusOK, response)
}

func (s *Server) resolve(method, escapedPath string) (operationID, resourceID string, pathKnown bool) {
	for _, operation := range s.contract.Operations {
		template := s.contract.BasePath + operation.Path
		values, match := matchTemplate(template, escapedPath)
		if !match {
			continue
		}
		pathKnown = true
		if method == strings.ToUpper(operation.Method) {
			return operation.OperationID, values["operation-id"], true
		}
	}
	return "", "", pathKnown
}

func matchTemplate(template, escapedPath string) (map[string]string, bool) {
	templateParts := strings.Split(strings.TrimPrefix(template, "/"), "/")
	pathParts := strings.Split(strings.TrimPrefix(escapedPath, "/"), "/")
	if len(templateParts) != len(pathParts) {
		return nil, false
	}
	values := make(map[string]string)
	for i, templatePart := range templateParts {
		pathPart := pathParts[i]
		if strings.HasPrefix(templatePart, "{") && strings.HasSuffix(templatePart, "}") {
			if pathPart == "" {
				return nil, false
			}
			value, err := url.PathUnescape(pathPart)
			if err != nil {
				return nil, false
			}
			values[strings.TrimSuffix(strings.TrimPrefix(templatePart, "{"), "}")] = value
			continue
		}
		if pathPart != templatePart {
			return nil, false
		}
	}
	return values, true
}

func writeAPIError(w http.ResponseWriter, status int) {
	writeJSON(w, status, map[string]any{
		"error_code":    int64(9001),
		"error_message": "fixture failure",
		"module_name":   "Policy",
		"details":       "contract mock",
	})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(value); err != nil {
		panic(err)
	}
}

func cloneMap(source map[string]any) map[string]any {
	if source == nil {
		return nil
	}
	clone := make(map[string]any, len(source))
	for key, value := range source {
		clone[key] = value
	}
	return clone
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}
