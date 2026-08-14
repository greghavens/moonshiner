// Package mockvcf provides a loopback-only fixture for the certificate update contract.
package mockvcf

import (
	"net/http"
	"net/http/httptest"
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
	mu     sync.RWMutex
	log    []Request
}

// New starts the loopback server.
func New(script Script) *Server {
	s := &Server{}
	s.server = httptest.NewServer(http.NotFoundHandler())
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
	copy(requests, s.log)
	return requests
}
