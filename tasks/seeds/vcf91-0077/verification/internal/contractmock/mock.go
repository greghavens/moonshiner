// Package contractmock provides a loopback-only NSX Policy service whose
// complete route allow-list is loaded from the focused protected contract.
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
	SchemaVersion int    `json:"schema_version"`
	Swagger       string `json:"swagger"`
	Info          struct {
		Title   string `json:"title"`
		Version string `json:"version"`
	} `json:"info"`
	BasePath string `json:"basePath"`
	Source   struct {
		Repository          string `json:"repository"`
		RepositoryCommitSHA string `json:"repository_commit_sha"`
		SpecBlobSHA         string `json:"spec_blob_sha"`
		SpecPath            string `json:"spec_path"`
		License             string `json:"license"`
		Derivation          string `json:"derivation"`
	} `json:"source"`
	SecurityDefinitions map[string]struct {
		Type string `json:"type"`
	} `json:"securityDefinitions"`
	Operations  map[string]Operation  `json:"operations"`
	Definitions map[string]Definition `json:"definitions"`
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

type Operation struct {
	OperationID string              `json:"operationId"`
	Method      string              `json:"method"`
	Path        string              `json:"path"`
	Consumes    []string            `json:"consumes"`
	Produces    []string            `json:"produces"`
	Parameters  []ContractParameter `json:"parameters"`
	Responses   map[string]Response `json:"responses"`
}

type ContractParameter struct {
	Name      string `json:"name"`
	In        string `json:"in"`
	Required  bool   `json:"required"`
	Type      string `json:"type"`
	Format    string `json:"format"`
	SchemaRef string `json:"schema_ref"`
	Minimum   *int64 `json:"minimum"`
	Maximum   *int64 `json:"maximum"`
	Default   any    `json:"default"`
}

type Response struct {
	Description string `json:"description"`
	SchemaRef   string `json:"schema_ref"`
}

type Definition struct {
	Type       string   `json:"type"`
	Required   []string `json:"required"`
	Properties map[string]struct {
		Type      string   `json:"type"`
		Enum      []string `json:"enum"`
		MaxLength int      `json:"maxLength"`
		SchemaRef string   `json:"schema_ref"`
		ItemsRef  string   `json:"items_ref"`
	} `json:"properties"`
}

// LoadContract validates enough of the projection for it to be a route and
// status authority for the mock.
func LoadContract(path string) (Contract, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Contract{}, err
	}
	var contract Contract
	if err := json.Unmarshal(data, &contract); err != nil {
		return Contract{}, err
	}
	if contract.SchemaVersion != 1 || contract.Swagger != "2.0" ||
		contract.Info.Title != "NSX Policy API" || contract.Info.Version != "9.1.0.0" ||
		contract.BasePath != "/policy/api/v1" {
		return Contract{}, fmt.Errorf("unexpected NSX Policy contract identity")
	}
	if len(contract.Operations) != 2 {
		return Contract{}, fmt.Errorf("focused contract must name exactly two operations")
	}
	wants := map[string]struct{ method, path string }{
		TagBulkUpdate:             {http.MethodPut, "/infra/tags/tag-operations/{operation-id}"},
		GetTagBulkOperationStatus: {http.MethodGet, "/infra/tags/tag-operations/{operation-id}/status"},
	}
	for id, want := range wants {
		op, ok := contract.Operations[id]
		if !ok || op.OperationID != id || op.Method != want.method || op.Path != want.path {
			return Contract{}, fmt.Errorf("unexpected focused operation %q", id)
		}
	}
	return contract, nil
}

// Script controls deterministic failures and the sequence of status values.
type Script struct {
	Statuses           []string
	PutHTTPStatus      int
	PollHTTPStatus     int
	SuccessContentType string
	TrailingStatusJSON bool
}

// Request is an owned request-log snapshot used by protected tests.
type Request struct {
	OperationID      string
	Method           string
	RequestURI       string
	EscapedPath      string
	RawQuery         string
	Header           http.Header
	ContentLength    int64
	TransferEncoding []string
	Body             []byte
}

// Server serves only operations declared in Contract and synchronizes all
// mutable fixture state and request-log access.
type Server struct {
	URL string

	contract Contract
	script   Script
	server   *httptest.Server
	client   *http.Client

	mu              sync.Mutex
	requests        []Request
	pollCount       int
	statusResponses int
	tags            map[string]map[string]any
}

// New prefers an ephemeral IPv4 loopback listener. If the environment denies
// sockets, the same handler is reached through an in-memory RoundTripper.
func New(t testing.TB, contractPath string, script Script) *Server {
	t.Helper()
	contract, err := LoadContract(contractPath)
	if err != nil {
		t.Fatalf("load contract mock: %v", err)
	}
	if len(script.Statuses) == 0 {
		script.Statuses = []string{"Success"}
	}
	if script.SuccessContentType == "" {
		script.SuccessContentType = "application/json"
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

func (s *Server) Client() *http.Client { return s.client }

// Requests returns a synchronized deep copy of the complete request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	requests := make([]Request, len(s.requests))
	for i, request := range s.requests {
		requests[i] = request
		requests[i].Header = request.Header.Clone()
		requests[i].TransferEncoding = append([]string(nil), request.TransferEncoding...)
		requests[i].Body = append([]byte(nil), request.Body...)
	}
	return requests
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	var body []byte
	if r.Body != nil {
		body, _ = io.ReadAll(io.LimitReader(r.Body, 1<<20))
		_ = r.Body.Close()
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
		OperationID:      operationID,
		Method:           r.Method,
		RequestURI:       requestURI,
		EscapedPath:      escapedPath,
		RawQuery:         r.URL.RawQuery,
		Header:           r.Header.Clone(),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
		Body:             append([]byte(nil), body...),
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
		http.NotFound(w, r)
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
	writeJSON(w, http.StatusOK, s.script.SuccessContentType, map[string]any{
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
	s.statusResponses++
	reverse := s.statusResponses%2 == 1
	s.mu.Unlock()
	if tag == nil {
		writeAPIError(w, http.StatusNotFound)
		return
	}
	apply, remove := outcomeGroups(status, reverse)
	response := map[string]any{
		"path":        "/infra/tags/tag-operations/" + operationID,
		"status":      status,
		"tag":         tag,
		"apply_to":    apply,
		"remove_from": remove,
	}
	writeJSON(w, http.StatusOK, s.script.SuccessContentType, response)
	if s.script.TrailingStatusJSON {
		_, _ = io.WriteString(w, `{}`)
	}
}

func outcomeGroups(status string, reverse bool) ([]map[string]any, []map[string]any) {
	apply := []map[string]any{
		statusGroup("VirtualMachine", resourceStatus("vm-b", "Success", ""), resourceStatus("vm-shared", "Success", "")),
		statusGroup("VirtualMachine", resourceStatus("vm-e", "Success", "")),
	}
	removeStatus, removeDetails := "Success", ""
	if status == "Error" {
		removeStatus, removeDetails = "Error", "resource was not found"
	}
	remove := []map[string]any{
		statusGroup("VirtualMachine", resourceStatus("vm-c", "Success", ""), resourceStatus("vm-shared", "Success", "")),
		statusGroup("VirtualMachine", resourceStatus("vm-d", removeStatus, removeDetails)),
	}
	if reverse {
		reverseGroups(apply)
		reverseGroups(remove)
	}
	return apply, remove
}

func statusGroup(resourceType string, statuses ...map[string]any) map[string]any {
	items := make([]any, len(statuses))
	for i := range statuses {
		items[i] = statuses[i]
	}
	return map[string]any{"resource_type": resourceType, "resource_tag_status": items}
}

func resourceStatus(id, status, details string) map[string]any {
	result := map[string]any{"resource_id": id, "tag_status": status}
	if details != "" {
		result["details"] = details
	}
	return result
}

func reverseGroups(groups []map[string]any) {
	for left, right := 0, len(groups)-1; left < right; left, right = left+1, right-1 {
		groups[left], groups[right] = groups[right], groups[left]
	}
	for _, group := range groups {
		items, _ := group["resource_tag_status"].([]any)
		for left, right := 0, len(items)-1; left < right; left, right = left+1, right-1 {
			items[left], items[right] = items[right], items[left]
		}
	}
}

func (s *Server) resolve(method, escapedPath string) (operationID, resourceID string, pathKnown bool) {
	for _, operation := range s.contract.Operations {
		template := s.contract.BasePath + operation.Path
		values, match := matchTemplate(template, escapedPath)
		if !match {
			continue
		}
		pathKnown = true
		if method == operation.Method {
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
	writeJSON(w, status, "application/json", map[string]any{
		"error_code":    int64(9001),
		"error_message": "fixture failure",
		"module_name":   "Policy",
		"details":       "contract mock",
	})
}

func writeJSON(w http.ResponseWriter, status int, contentType string, value any) {
	w.Header().Set("Content-Type", contentType)
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
