// Package contractmock provides a loopback-only mock for the single operation
// named by docs/contract.json.
package contractmock

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"sync"
)

const DeploymentsPath = "/deployment/api/deployments"

// Deployment mirrors the response fields exercised by the contract tests.
type Deployment struct {
	ID        string `json:"id"`
	Name      string `json:"name"`
	ProjectID string `json:"projectId"`
	Status    string `json:"status"`
	CreatedAt string `json:"createdAt"`
}

// Page is a PageDeployment fixture.
type Page struct {
	Content          []Deployment `json:"content"`
	Number           int          `json:"number"`
	NumberOfElements int          `json:"numberOfElements"`
	Size             int          `json:"size"`
	TotalElements    int          `json:"totalElements"`
	TotalPages       int          `json:"totalPages"`
	First            bool         `json:"first"`
	Last             bool         `json:"last"`
	Empty            bool         `json:"empty"`
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

// Server serves only GET /deployment/api/deployments and keeps a race-safe
// request log.
type Server struct {
	server   *httptest.Server
	pages    map[int]Page
	mu       sync.Mutex
	requests []Request
}

// New starts a loopback server backed by the supplied zero-based pages.
func New(pages map[int]Page) *Server {
	s := &Server{pages: clonePages(pages)}
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

	if r.URL.Path != DeploymentsPath {
		http.NotFound(w, r)
		return
	}
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	pageNumber := 0
	if raw := r.URL.Query().Get("page"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil {
			http.Error(w, "invalid page", http.StatusBadRequest)
			return
		}
		pageNumber = parsed
	}
	page, ok := s.pages[pageNumber]
	if !ok {
		http.Error(w, "page not found", http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(page)
}

func clonePages(pages map[int]Page) map[int]Page {
	cloned := make(map[int]Page, len(pages))
	for number, page := range pages {
		page.Content = append([]Deployment(nil), page.Content...)
		cloned[number] = page
	}
	return cloned
}
