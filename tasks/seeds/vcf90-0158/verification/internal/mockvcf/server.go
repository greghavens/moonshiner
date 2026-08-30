// Package mockvcf provides a contract-pinned, loopback-only VCF Automation
// server for tests. It implements exactly the two operations named by the
// supplied docs/contract.json and records the received wire requests.
package mockvcf

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
)

const (
	submitOperation = "Submit Deployment Action Request"
	getOperation    = "Get Request"
)

// Contract is the subset of docs/contract.json used to pin the mock routes.
type Contract struct {
	Operations []Operation `json:"operations"`
}

// Operation describes one REST operation used by this fixture.
type Operation struct {
	Name         string `json:"name"`
	Method       string `json:"method"`
	PathTemplate string `json:"path_template"`
}

// Scenario controls the asynchronous status sequence returned by the mock.
type Scenario struct {
	RequestID    string
	SubmitStatus string
	PollStatuses []string
}

// LoggedRequest is a copy of one HTTP request received by the mock.
type LoggedRequest struct {
	Method   string
	Path     string
	RawQuery string
	Header   http.Header
	Body     []byte
}

// Server wraps a loopback httptest server and its race-safe request log.
type Server struct {
	URL string

	testServer *httptest.Server
	contract   Contract
	scenario   Scenario

	mu        sync.Mutex
	requests  []LoggedRequest
	pollIndex int
}

// New loads contractPath, verifies that it names only the fixture's two
// operations, and starts a loopback server.
func New(t testing.TB, contractPath string, scenario Scenario) *Server {
	t.Helper()

	raw, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read mock contract: %v", err)
	}

	var contract Contract
	if err := json.Unmarshal(raw, &contract); err != nil {
		t.Fatalf("decode mock contract: %v", err)
	}
	validateContract(t, contract)

	if scenario.RequestID == "" {
		scenario.RequestID = "request-42"
	}
	if scenario.SubmitStatus == "" {
		scenario.SubmitStatus = "INPROGRESS"
	}

	s := &Server{contract: contract, scenario: scenario}
	s.testServer = httptest.NewServer(http.HandlerFunc(s.serveHTTP))
	s.URL = s.testServer.URL
	t.Cleanup(s.testServer.Close)
	return s
}

func validateContract(t testing.TB, contract Contract) {
	t.Helper()
	if len(contract.Operations) != 2 {
		t.Fatalf("mock contract must name exactly two operations, got %d", len(contract.Operations))
	}
	want := map[string]Operation{
		submitOperation: {
			Name:         submitOperation,
			Method:       http.MethodPost,
			PathTemplate: "/deployment/api/deployments/{deploymentId}/requests",
		},
		getOperation: {
			Name:         getOperation,
			Method:       http.MethodGet,
			PathTemplate: "/deployment/api/requests/{requestId}",
		},
	}
	for _, operation := range contract.Operations {
		expected, ok := want[operation.Name]
		if !ok || operation != expected {
			t.Fatalf("mock does not implement contract operation %+v", operation)
		}
		delete(want, operation.Name)
	}
	if len(want) != 0 {
		t.Fatalf("mock contract is missing operations: %+v", want)
	}
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "read request", http.StatusBadRequest)
		return
	}
	s.appendRequest(LoggedRequest{
		Method:   r.Method,
		Path:     r.URL.EscapedPath(),
		RawQuery: r.URL.RawQuery,
		Header:   r.Header.Clone(),
		Body:     append([]byte(nil), body...),
	})

	submitPrefix := "/deployment/api/deployments/"
	if r.Method == http.MethodPost && strings.HasPrefix(r.URL.Path, submitPrefix) && strings.HasSuffix(r.URL.Path, "/requests") {
		middle := strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, submitPrefix), "/requests")
		if middle != "" && !strings.Contains(middle, "/") {
			s.writeRequest(w, s.scenario.SubmitStatus)
			return
		}
	}

	requestPrefix := "/deployment/api/requests/"
	if r.Method == http.MethodGet && r.URL.Path == requestPrefix+s.scenario.RequestID {
		s.writeRequest(w, s.nextPollStatus())
		return
	}

	http.NotFound(w, r)
}

func (s *Server) writeRequest(w http.ResponseWriter, status string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"id":             s.scenario.RequestID,
		"name":           "deployment action",
		"requestedBy":    "fixture-user",
		"status":         status,
		"completedTasks": 0,
		"totalTasks":     1,
	})
}

func (s *Server) nextPollStatus() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.scenario.PollStatuses) == 0 {
		return s.scenario.SubmitStatus
	}
	index := s.pollIndex
	if index >= len(s.scenario.PollStatuses) {
		index = len(s.scenario.PollStatuses) - 1
	} else {
		s.pollIndex++
	}
	return s.scenario.PollStatuses[index]
}

func (s *Server) appendRequest(request LoggedRequest) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, request)
}

// Requests returns an independent snapshot of the received request log.
func (s *Server) Requests() []LoggedRequest {
	s.mu.Lock()
	defer s.mu.Unlock()
	requests := make([]LoggedRequest, len(s.requests))
	for i, request := range s.requests {
		requests[i] = request
		requests[i].Header = request.Header.Clone()
		requests[i].Body = append([]byte(nil), request.Body...)
	}
	return requests
}

// AssertLoopback documents and checks the fixture's no-live-endpoint property.
func (s *Server) AssertLoopback(t testing.TB) {
	t.Helper()
	if !strings.HasPrefix(s.URL, "http://127.0.0.1:") && !strings.HasPrefix(s.URL, "http://[::1]:") {
		t.Fatalf("mock is not loopback: %s", s.URL)
	}
}

func (request LoggedRequest) String() string {
	return fmt.Sprintf("%s %s body=%q", request.Method, request.Path, request.Body)
}
