// Package contractmock provides a loopback-only mock for the operations named
// by docs/contract.json.
package contractmock

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
)

const (
	// NamesPath is the contracted name-existence precheck path.
	NamesPath = "/deployment/api/deployments/names"
	// DeploymentsPath is the base of the contracted deployment update path.
	DeploymentsPath = "/deployment/api/deployments"
)

// Deployment mirrors the response fields exercised by the contract tests.
type Deployment struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Description string `json:"description"`
	IconID      string `json:"iconId"`
	Status      string `json:"status"`
}

// Config supplies canned responses for the two contracted operations. A zero
// status uses the normal workflow status: 404 for the check and 200 for PATCH.
type Config struct {
	CheckStatus      int
	PatchStatus      int
	PatchResponse    Deployment
	PatchResponseRaw []byte
}

// Request records the observable wire details used by the verifier.
type Request struct {
	Method        string
	RequestURI    string
	Authorization string
	Accept        string
	ContentType   string
	Body          []byte
}

// Server serves only the two operations in docs/contract.json and keeps a
// race-safe request log.
type Server struct {
	server *httptest.Server
	config Config

	mu       sync.Mutex
	requests []Request
}

// New starts a loopback server backed by config.
func New(config Config) *Server {
	if config.CheckStatus == 0 {
		config.CheckStatus = http.StatusNotFound
	}
	if config.PatchStatus == 0 {
		config.PatchStatus = http.StatusOK
	}
	s := &Server{config: config}
	s.server = httptest.NewServer(http.HandlerFunc(s.serveHTTP))
	return s
}

// URL returns the loopback base URL.
func (s *Server) URL() string { return s.server.URL }

// Client returns the server's loopback HTTP client.
func (s *Server) Client() *http.Client { return s.server.Client() }

// Close stops the server.
func (s *Server) Close() { s.server.Close() }

// Requests returns a deep copy of the request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.requests))
	for i, request := range s.requests {
		out[i] = request
		out[i].Body = append([]byte(nil), request.Body...)
	}
	return out
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	s.mu.Lock()
	s.requests = append(s.requests, Request{
		Method:        r.Method,
		RequestURI:    r.RequestURI,
		Authorization: r.Header.Get("Authorization"),
		Accept:        r.Header.Get("Accept"),
		ContentType:   r.Header.Get("Content-Type"),
		Body:          body,
	})
	s.mu.Unlock()

	switch {
	case r.URL.Path == NamesPath:
		if r.Method != http.MethodGet {
			w.Header().Set("Allow", http.MethodGet)
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		w.WriteHeader(s.config.CheckStatus)
	case isDeploymentPath(r.URL.Path):
		if r.Method != http.MethodPatch {
			w.Header().Set("Allow", http.MethodPatch)
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(s.config.PatchStatus)
		if s.config.PatchResponseRaw != nil {
			_, _ = w.Write(s.config.PatchResponseRaw)
			return
		}
		_ = json.NewEncoder(w).Encode(s.config.PatchResponse)
	default:
		http.NotFound(w, r)
	}
}

func isDeploymentPath(path string) bool {
	id := strings.TrimPrefix(path, DeploymentsPath+"/")
	return id != path && id != "" && !strings.Contains(id, "/")
}
