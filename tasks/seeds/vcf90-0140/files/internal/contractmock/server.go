// Package contractmock provides a loopback-only HTTP fixture for the two
// operations recorded in docs/contract.json.
package contractmock

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
)

const (
	BasePath                 = "/api/ni"
	UpdateVcenterOperationID = "updateVcenter"
	EnableVcenterOperationID = "enableVcenter"
)

// Plan selects the HTTP status returned by each contracted operation. A zero
// value uses the operation's documented success status.
type Plan struct {
	UpdateStatus int
	EnableStatus int
}

// Request is an immutable snapshot of one request received by the mock.
type Request struct {
	Method      string
	EscapedPath string
	RawQuery    string
	Header      http.Header
	Body        []byte
}

// Server is a loopback HTTP server with a race-safe request log.
type Server struct {
	httpServer *httptest.Server
	plan       Plan

	mu       sync.Mutex
	requests []Request
}

// New starts a loopback server which serves only updateVcenter and
// enableVcenter.
func New(plan Plan) *Server {
	if plan.UpdateStatus == 0 {
		plan.UpdateStatus = http.StatusOK
	}
	if plan.EnableStatus == 0 {
		plan.EnableStatus = http.StatusOK
	}

	s := &Server{plan: plan}
	s.httpServer = httptest.NewServer(http.HandlerFunc(s.serveHTTP))
	return s
}

// URL returns the mock server origin.
func (s *Server) URL() string {
	return s.httpServer.URL
}

// Client returns an HTTP client configured for this loopback server.
func (s *Server) Client() *http.Client {
	return s.httpServer.Client()
}

// Close stops the loopback server.
func (s *Server) Close() {
	s.httpServer.Close()
}

// Requests returns a deep copy of the request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()

	requests := make([]Request, len(s.requests))
	for i, request := range s.requests {
		requests[i] = Request{
			Method:      request.Method,
			EscapedPath: request.EscapedPath,
			RawQuery:    request.RawQuery,
			Header:      request.Header.Clone(),
			Body:        append([]byte(nil), request.Body...),
		}
	}
	return requests
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	_ = r.Body.Close()

	escapedPath := r.URL.EscapedPath()
	s.mu.Lock()
	s.requests = append(s.requests, Request{
		Method:      r.Method,
		EscapedPath: escapedPath,
		RawQuery:    r.URL.RawQuery,
		Header:      r.Header.Clone(),
		Body:        append([]byte(nil), body...),
	})
	s.mu.Unlock()

	prefix := BasePath + "/data-sources/vcenters/"
	if !strings.HasPrefix(escapedPath, prefix) {
		http.NotFound(w, r)
		return
	}
	remainder := strings.TrimPrefix(escapedPath, prefix)
	if remainder == "" {
		http.NotFound(w, r)
		return
	}

	if r.Method == http.MethodPut && !strings.Contains(remainder, "/") {
		if s.plan.UpdateStatus == http.StatusOK {
			w.Header().Set("Content-Type", "application/json")
		}
		w.WriteHeader(s.plan.UpdateStatus)
		if s.plan.UpdateStatus == http.StatusOK {
			_, _ = w.Write([]byte("{}"))
		}
		return
	}

	if r.Method == http.MethodPost && strings.HasSuffix(remainder, "/enable") &&
		strings.Count(remainder, "/") == 1 {
		w.WriteHeader(s.plan.EnableStatus)
		return
	}

	http.NotFound(w, r)
}
