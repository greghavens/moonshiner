// Package mockvcenter provides a loopback HTTP fixture for the reduced VCF 9.1
// vCenter contract in docs/contract.json.
package mockvcenter

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"slices"
	"strconv"
	"strings"
	"sync"
)

const (
	CloneTaskOperationID = "Vcenter.VM_clone$Task"
	GetTaskOperationID   = "Cis.Tasks_get"
)

// Status is a Cis.Task.Status value from the pinned contract.
type Status string

const (
	StatusPending   Status = "PENDING"
	StatusRunning   Status = "RUNNING"
	StatusBlocked   Status = "BLOCKED"
	StatusSucceeded Status = "SUCCEEDED"
	StatusFailed    Status = "FAILED"
)

// Scenario describes one deterministic asynchronous clone.
type Scenario struct {
	SessionID string
	TaskID    string
	ResultVM  string
	Statuses  []Status
}

// Request is an immutable snapshot of an incoming request.
type Request struct {
	Method   string
	Path     string
	RawQuery string
	Header   http.Header
	Body     []byte
}

// Server is a loopback-only mock. Requests returns its race-safe request log.
type Server struct {
	httpServer *httptest.Server
	httpClient *http.Client
	origin     string
	scenario   Scenario

	mu        sync.Mutex
	requests  []Request
	pollCount int
}

// New starts a loopback server for scenario.
func New(scenario Scenario) (*Server, error) {
	if scenario.SessionID == "" || scenario.TaskID == "" || scenario.ResultVM == "" {
		return nil, errors.New("session ID, task ID, and result VM are required")
	}
	if len(scenario.Statuses) == 0 {
		return nil, errors.New("at least one task status is required")
	}
	for _, status := range scenario.Statuses {
		switch status {
		case StatusPending, StatusRunning, StatusBlocked, StatusSucceeded, StatusFailed:
		default:
			return nil, fmt.Errorf("unsupported task status %q", status)
		}
	}
	last := scenario.Statuses[len(scenario.Statuses)-1]
	if last != StatusSucceeded && last != StatusFailed {
		return nil, errors.New("last task status must be terminal")
	}

	server := &Server{scenario: scenario}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err == nil {
		server.httpServer = httptest.NewUnstartedServer(http.HandlerFunc(server.serveHTTP))
		server.httpServer.Listener = listener
		server.httpServer.Start()
		server.origin = server.httpServer.URL
		server.httpClient = server.httpServer.Client()
	} else {
		// Some code-execution sandboxes deny socket creation, including loopback.
		// Exercise the identical HTTP handler through a RoundTripper in that case.
		server.origin = "http://127.0.0.1"
		server.httpClient = &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
			recorder := httptest.NewRecorder()
			server.serveHTTP(recorder, request)
			return recorder.Result(), nil
		})}
	}
	return server, nil
}

// URL returns the loopback origin, without the contract's /api base path.
func (s *Server) URL() string {
	return s.origin
}

// Client returns an HTTP client connected to this mock. On systems that permit
// loopback sockets it is the httptest server client.
func (s *Server) Client() *http.Client {
	return s.httpClient
}

// Close stops the loopback server.
func (s *Server) Close() {
	if s.httpServer != nil {
		s.httpServer.Close()
	}
}

// Requests returns a deep copy of the requests received so far.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()

	out := make([]Request, len(s.requests))
	for i, request := range s.requests {
		out[i] = request
		out[i].Header = request.Header.Clone()
		out[i].Body = slices.Clone(request.Body)
	}
	return out
}

// OperationIDs returns the only operations served by this fixture.
func OperationIDs() []string {
	return []string{CloneTaskOperationID, GetTaskOperationID}
}

func (s *Server) serveHTTP(response http.ResponseWriter, request *http.Request) {
	var (
		body    []byte
		readErr error
	)
	if request.Body != nil {
		body, readErr = io.ReadAll(request.Body)
	}
	s.record(request, body)
	if readErr != nil {
		writeError(response, http.StatusBadRequest, "unable to read request")
		return
	}
	if request.Header.Get("vmware-api-session-id") != s.scenario.SessionID {
		writeError(response, http.StatusUnauthorized, "invalid session")
		return
	}

	switch {
	case request.Method == http.MethodPost &&
		request.URL.Path == "/api/vcenter/vm" &&
		request.URL.RawQuery == "action=clone&vmw-task=true":
		s.clone(response, request, body)
	case request.Method == http.MethodGet &&
		request.URL.Path == "/api/cis/tasks/"+url.PathEscape(s.scenario.TaskID):
		s.getTask(response, request)
	default:
		writeError(response, http.StatusNotFound, "operation is outside the pinned contract")
	}
}

func (s *Server) record(request *http.Request, body []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, Request{
		Method:   request.Method,
		Path:     request.URL.Path,
		RawQuery: request.URL.RawQuery,
		Header:   request.Header.Clone(),
		Body:     slices.Clone(body),
	})
}

func (s *Server) clone(response http.ResponseWriter, request *http.Request, body []byte) {
	if request.Header.Get("Content-Type") != "application/json" {
		writeError(response, http.StatusUnsupportedMediaType, "content type must be application/json")
		return
	}

	var document map[string]json.RawMessage
	if err := json.Unmarshal(body, &document); err != nil {
		writeError(response, http.StatusBadRequest, "request body must be a JSON object")
		return
	}
	allowed := map[string]bool{
		"source": true, "name": true, "placement": true,
		"disks_to_remove": true, "disks_to_update": true,
		"power_on": true, "guest_customization_spec": true,
	}
	for property := range document {
		if !allowed[property] {
			writeError(response, http.StatusBadRequest, "unknown clone property")
			return
		}
	}
	for _, required := range []string{"source", "name"} {
		var value string
		raw, ok := document[required]
		if !ok || json.Unmarshal(raw, &value) != nil || value == "" {
			writeError(response, http.StatusBadRequest, required+" is required")
			return
		}
	}

	writeJSON(response, http.StatusAccepted, s.scenario.TaskID)
}

func (s *Server) getTask(response http.ResponseWriter, request *http.Request) {
	if !validTaskQuery(request.URL.Query()) {
		writeError(response, http.StatusBadRequest, "invalid task query")
		return
	}

	s.mu.Lock()
	index := s.pollCount
	if index >= len(s.scenario.Statuses) {
		index = len(s.scenario.Statuses) - 1
	}
	status := s.scenario.Statuses[index]
	s.pollCount++
	s.mu.Unlock()

	info := map[string]any{
		"description": map[string]any{
			"id":              "com.vmware.vcenter.vm.clone",
			"default_message": "Clone virtual machine",
			"args":            []string{},
		},
		"service":    "com.vmware.vcenter.VM",
		"operation":  "clone",
		"status":     status,
		"cancelable": false,
	}
	switch status {
	case StatusSucceeded:
		info["result"] = s.scenario.ResultVM
	case StatusFailed:
		info["error"] = map[string]any{
			"error_type": "CLONE_FAILED",
			"messages": []map[string]any{{
				"id":              "mock.clone.failed",
				"default_message": "clone failed",
				"args":            []string{},
			}},
		}
	}
	writeJSON(response, http.StatusOK, info)
}

func validTaskQuery(query url.Values) bool {
	for key, values := range query {
		if key != "return_all" && key != "exclude_result" {
			return false
		}
		if len(values) != 1 {
			return false
		}
		if _, err := strconv.ParseBool(values[0]); err != nil {
			return false
		}
	}
	return true
}

func writeJSON(response http.ResponseWriter, status int, value any) {
	response.Header().Set("Content-Type", "application/json")
	response.WriteHeader(status)
	_ = json.NewEncoder(response).Encode(value)
}

func writeError(response http.ResponseWriter, status int, message string) {
	writeJSON(response, status, map[string]any{
		"error_type": strings.ToUpper(strings.ReplaceAll(http.StatusText(status), " ", "_")),
		"messages": []map[string]any{{
			"id":              "mock.error",
			"default_message": message,
			"args":            []string{},
		}},
	})
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}
