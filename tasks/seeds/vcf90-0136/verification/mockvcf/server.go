// Package mockvcf provides a loopback-only fixture for the certificate update contract.
package mockvcf

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"

	"vcfnetworks/networks"
)

// Script defines the accepted response and subsequent poll responses.
type Script struct {
	Initial networks.CertificateUpdateStatus
	Polls   []networks.CertificateUpdateStatus
}

// Request records an HTTP request received by the mock.
type Request struct {
	Method     string
	RequestURI string
	Header     http.Header
	Body       []byte
}

// Server is a loopback HTTP server with a concurrency-safe request log.
type Server struct {
	server *httptest.Server
	script Script
	poll   int
	mu     sync.RWMutex
	log    []Request
}

// New starts the loopback server.
func New(script Script) *Server {
	cloned := Script{Initial: script.Initial, Polls: append([]networks.CertificateUpdateStatus(nil), script.Polls...)}
	s := &Server{script: cloned}
	// The two routes below correspond exactly to the operations in docs/contract.json.
	s.server = httptest.NewServer(http.HandlerFunc(s.serveHTTP))
	return s
}

// URL returns the loopback server URL.
func (s *Server) URL() string { return s.server.URL }

// Client returns an HTTP client configured for this loopback server.
func (s *Server) Client() *http.Client { return s.server.Client() }

// Close stops the loopback server.
func (s *Server) Close() { s.server.Close() }

// Requests returns a detached snapshot of the request log.
func (s *Server) Requests() []Request {
	s.mu.RLock()
	defer s.mu.RUnlock()
	requests := make([]Request, len(s.log))
	for index, request := range s.log {
		requests[index] = Request{
			Method:     request.Method,
			RequestURI: request.RequestURI,
			Header:     request.Header.Clone(),
			Body:       append([]byte(nil), request.Body...),
		}
	}
	return requests
}

func (s *Server) serveHTTP(writer http.ResponseWriter, request *http.Request) {
	body, err := io.ReadAll(request.Body)
	if err != nil {
		http.Error(writer, "read request", http.StatusBadRequest)
		return
	}
	s.mu.Lock()
	s.log = append(s.log, Request{
		Method:     request.Method,
		RequestURI: request.URL.RequestURI(),
		Header:     request.Header.Clone(),
		Body:       append([]byte(nil), body...),
	})
	s.mu.Unlock()

	escapedPath := request.URL.EscapedPath()
	if request.URL.RawQuery == "" && request.Method == http.MethodPut && matchesOneID(escapedPath, "/api/ni/settings/certificates/") {
		writeJSON(writer, http.StatusAccepted, s.script.Initial)
		return
	}
	if request.URL.RawQuery == "" && request.Method == http.MethodGet && matchesOneID(escapedPath, "/api/ni/settings/certificates/status/") {
		s.servePoll(writer)
		return
	}
	http.NotFound(writer, request)
}

func matchesOneID(path, prefix string) bool {
	if !strings.HasPrefix(path, prefix) {
		return false
	}
	id := strings.TrimPrefix(path, prefix)
	return id != "" && !strings.Contains(id, "/")
}

func (s *Server) servePoll(writer http.ResponseWriter) {
	s.mu.Lock()
	if s.poll >= len(s.script.Polls) {
		s.mu.Unlock()
		http.Error(writer, "poll script exhausted", http.StatusInternalServerError)
		return
	}
	status := s.script.Polls[s.poll]
	s.poll++
	s.mu.Unlock()
	writeJSON(writer, http.StatusOK, status)
}

func writeJSON(writer http.ResponseWriter, status int, value any) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(value)
}
