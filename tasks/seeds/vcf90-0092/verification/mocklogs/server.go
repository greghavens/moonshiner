// Package mocklogs provides a loopback server for the two operations in the
// pinned VCF Operations for Logs contract.
package mocklogs

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
)

const (
	OperationPostSessions  = "POST_sessions"
	OperationGetEventsPath = "GET_events-+path"
)

// Step is one scripted response for a named contract operation.
type Step struct {
	OperationID string
	StatusCode  int
	Body        any
}

// Request records the observable request wire.
type Request struct {
	Method     string
	RequestURI string
	Header     http.Header
	Body       string
}

// Server is a loopback-only scripted VCF Operations for Logs server.
type Server struct {
	server *httptest.Server

	mu       sync.Mutex
	steps    []Step
	nextStep int
	requests []Request
}

// New starts a loopback server with the supplied response script.
func New(steps []Step) *Server {
	s := &Server{steps: append([]Step(nil), steps...)}
	s.server = httptest.NewServer(http.HandlerFunc(s.serveHTTP))
	return s
}

// URL returns the loopback appliance root URL.
func (s *Server) URL() string { return s.server.URL }

// Client returns the HTTP client associated with the test server.
func (s *Server) Client() *http.Client { return s.server.Client() }

// Close stops the loopback server.
func (s *Server) Close() { s.server.Close() }

// Requests returns a deep copy of the request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()

	requests := make([]Request, len(s.requests))
	for i, request := range s.requests {
		requests[i] = request
		requests[i].Header = request.Header.Clone()
	}
	return requests
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	record := Request{
		Method:     r.Method,
		RequestURI: r.RequestURI,
		Header:     r.Header.Clone(),
		Body:       string(body),
	}

	s.mu.Lock()
	s.requests = append(s.requests, record)
	operationID, ok := contractOperation(r)
	if !ok {
		s.mu.Unlock()
		http.NotFound(w, r)
		return
	}
	if s.nextStep >= len(s.steps) || s.steps[s.nextStep].OperationID != operationID {
		s.mu.Unlock()
		http.Error(w, "unexpected contract operation", http.StatusInternalServerError)
		return
	}
	step := s.steps[s.nextStep]
	s.nextStep++
	s.mu.Unlock()

	status := step.StatusCode
	if status == 0 {
		status = http.StatusOK
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if step.Body != nil {
		_ = json.NewEncoder(w).Encode(step.Body)
	}
}

func contractOperation(r *http.Request) (string, bool) {
	switch {
	case r.Method == http.MethodPost && r.URL.Path == "/api/v2/sessions":
		return OperationPostSessions, true
	case r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/api/v2/events/") && strings.TrimPrefix(r.URL.Path, "/api/v2/events/") != "":
		return OperationGetEventsPath, true
	default:
		return "", false
	}
}
