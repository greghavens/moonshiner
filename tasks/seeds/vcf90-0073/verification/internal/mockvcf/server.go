// Package mockvcf supplies a loopback implementation of the single operation
// retained in docs/contract.json. It is a test fixture, not a general VCF API.
package mockvcf

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
)

const (
	OperationID = "getSymptomDefinitions"
	requestPath = "/suite-api/api/symptomdefinitions"
)

type SymptomDefinition struct {
	ID                        string          `json:"id,omitempty"`
	Name                      string          `json:"name"`
	AdapterKindKey            string          `json:"adapterKindKey"`
	ResourceKindKey           string          `json:"resourceKindKey"`
	WaitCycles                *int            `json:"waitCycles,omitempty"`
	CancelCycles              *int            `json:"cancelCycles,omitempty"`
	RealtimeMonitoringEnabled *bool           `json:"realtimeMonitoringEnabled,omitempty"`
	State                     json.RawMessage `json:"state"`
}

type Request struct {
	Method     string
	RequestURI string
	Header     http.Header
	Body       string
}

type Server struct {
	httpServer  *httptest.Server
	definitions []SymptomDefinition
	mu          sync.Mutex
	requests    []Request
}

func New(definitions []SymptomDefinition) *Server {
	s := &Server{definitions: cloneDefinitions(definitions)}
	s.httpServer = httptest.NewServer(http.HandlerFunc(s.serveHTTP))
	return s
}

func (s *Server) URL() string { return s.httpServer.URL }

func (s *Server) Client() *http.Client { return s.httpServer.Client() }

func (s *Server) Close() { s.httpServer.Close() }

func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.requests))
	for i, req := range s.requests {
		out[i] = req
		out[i].Header = req.Header.Clone()
	}
	return out
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	s.mu.Lock()
	s.requests = append(s.requests, Request{
		Method:     r.Method,
		RequestURI: r.RequestURI,
		Header:     r.Header.Clone(),
		Body:       string(body),
	})
	s.mu.Unlock()

	if r.URL.Path != requestPath {
		http.NotFound(w, r)
		return
	}
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if r.Header.Get("Authorization") == "" {
		http.Error(w, "missing Authorization", http.StatusUnauthorized)
		return
	}

	query := r.URL.Query()
	allowed := map[string]bool{
		"adapterKind":  true,
		"resourceKind": true,
		"id":           true,
		"name":         true,
		"page":         true,
		"pageSize":     true,
	}
	for key := range query {
		if !allowed[key] {
			http.Error(w, "unknown query field", http.StatusBadRequest)
			return
		}
	}

	page, ok := integerQuery(query.Get("page"), 0, 0)
	if !ok {
		http.Error(w, "invalid page", http.StatusBadRequest)
		return
	}
	pageSize, ok := integerQuery(query.Get("pageSize"), 1000, 1)
	if !ok {
		http.Error(w, "invalid pageSize", http.StatusBadRequest)
		return
	}

	filtered := filter(s.definitions, query["adapterKind"], query["resourceKind"], query["id"], query["name"])
	start := page * pageSize
	if start > len(filtered) {
		start = len(filtered)
	}
	end := start + pageSize
	if end > len(filtered) {
		end = len(filtered)
	}

	response := struct {
		PageInfo struct {
			TotalCount int `json:"totalCount"`
			Page       int `json:"page"`
			PageSize   int `json:"pageSize"`
		} `json:"pageInfo"`
		SymptomDefinitions []SymptomDefinition `json:"symptomDefinitions"`
	}{}
	response.PageInfo.TotalCount = len(filtered)
	response.PageInfo.Page = page
	response.PageInfo.PageSize = pageSize
	response.SymptomDefinitions = filtered[start:end]

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(response)
}

func integerQuery(value string, defaultValue, minimum int) (int, bool) {
	if value == "" {
		return defaultValue, true
	}
	n, err := strconv.Atoi(value)
	return n, err == nil && n >= minimum
}

func filter(definitions []SymptomDefinition, adapterKinds, resourceKinds, ids, names []string) []SymptomDefinition {
	var out []SymptomDefinition
	for _, definition := range definitions {
		if !matchesOne(definition.AdapterKindKey, adapterKinds, false) ||
			!matchesOne(definition.ResourceKindKey, resourceKinds, false) ||
			!matchesOne(definition.ID, ids, false) ||
			!matchesOne(definition.Name, names, true) {
			continue
		}
		out = append(out, cloneDefinition(definition))
	}
	return out
}

func matchesOne(value string, filters []string, substring bool) bool {
	if len(filters) == 0 {
		return true
	}
	for _, candidate := range filters {
		if substring {
			if strings.Contains(strings.ToLower(value), strings.ToLower(candidate)) {
				return true
			}
		} else if value == candidate {
			return true
		}
	}
	return false
}

func cloneDefinitions(in []SymptomDefinition) []SymptomDefinition {
	out := make([]SymptomDefinition, len(in))
	for i, definition := range in {
		out[i] = cloneDefinition(definition)
	}
	return out
}

func cloneDefinition(in SymptomDefinition) SymptomDefinition {
	out := in
	out.State = append(json.RawMessage(nil), in.State...)
	return out
}
