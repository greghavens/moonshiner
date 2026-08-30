// Package mockvcf provides the contract-pinned loopback VCF Log Management
// service used by acceptance tests. It never contacts a VMware endpoint.
package mockvcf

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"sync"

	contractdoc "example.com/vcfopslogs/docs"
)

type Request struct {
	Method     string
	Path       string
	RawQuery   string
	RequestURI string
	Header     http.Header
	Body       []byte
}

type Server struct {
	server   *httptest.Server
	endpoint contractdoc.Endpoint
	token    string

	mu       sync.Mutex
	requests []Request
}

type agentGroup struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Info        string `json:"info,omitempty"`
	AgentConfig string `json:"agentConfig,omitempty"`
	MPID        string `json:"mpId,omitempty"`
	AutoUpdate  bool   `json:"autoUpdate"`
}

type page struct {
	Content          []agentGroup `json:"content"`
	Empty            bool         `json:"empty"`
	First            bool         `json:"first"`
	Last             bool         `json:"last"`
	Number           int          `json:"number"`
	NumberOfElements int          `json:"numberOfElements"`
	Size             int          `json:"size"`
	TotalElements    int          `json:"totalElements"`
	TotalPages       int          `json:"totalPages"`
}

var collection = []agentGroup{
	{ID: "group-zeta", Name: "Zeta collectors", Info: "late page order", MPID: "mp-5"},
	{ID: "group-alpha-2", Name: "Alpha collectors", Info: "secondary", AutoUpdate: true},
	{ID: "group-beta", Name: "Beta collectors", AgentConfig: "beta.conf"},
	{ID: "group-alpha-1", Name: "Alpha collectors", Info: "primary", MPID: "mp-1"},
	{ID: "group-delta", Name: "Delta collectors", AutoUpdate: true},
}

func New(expectedToken string) (*Server, error) {
	doc, err := contractdoc.Load()
	if err != nil {
		return nil, err
	}
	if err := doc.ValidatePinnedSubset(); err != nil {
		return nil, fmt.Errorf("invalid embedded contract: %w", err)
	}
	s := &Server{endpoint: doc.Endpoints()[0], token: expectedToken}
	s.server = httptest.NewServer(http.HandlerFunc(s.serveHTTP))
	return s, nil
}

func (s *Server) URL() string {
	return s.server.URL
}

func (s *Server) Client() *http.Client {
	return s.server.Client()
}

func (s *Server) Close() {
	s.server.Close()
}

func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.requests))
	for i, req := range s.requests {
		out[i] = req
		out[i].Header = req.Header.Clone()
		out[i].Body = append([]byte(nil), req.Body...)
	}
	return out
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	s.mu.Lock()
	s.requests = append(s.requests, Request{
		Method: r.Method, Path: r.URL.Path, RawQuery: r.URL.RawQuery,
		RequestURI: r.RequestURI, Header: r.Header.Clone(),
		Body: append([]byte(nil), body...),
	})
	s.mu.Unlock()

	if r.URL.Path != s.endpoint.Path {
		http.NotFound(w, r)
		return
	}
	if r.Method != s.endpoint.Method {
		w.Header().Set("Allow", s.endpoint.Method)
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if r.Header.Get("X-JWT-Token") != s.token {
		writeJSON(w, http.StatusForbidden, map[string]string{
			"errorCode": "SECURITY_ERROR", "errorMessage": "token rejected",
		})
		return
	}
	if len(body) != 0 || r.Header.Get("Content-Type") != "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"errorCode": "API_ERROR", "errorMessage": "GET must not carry a body",
		})
		return
	}

	query := r.URL.Query()
	for key := range query {
		if key != "page" && key != "size" && key != "sort" {
			writeJSON(w, http.StatusBadRequest, map[string]string{
				"errorCode": "API_ERROR", "errorMessage": "unknown query member " + key,
			})
			return
		}
	}
	if len(query["page"]) != 1 || len(query["size"]) != 1 {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"errorCode": "API_ERROR", "errorMessage": "page and size are required once",
		})
		return
	}
	for _, value := range query["sort"] {
		if value == "" {
			writeJSON(w, http.StatusBadRequest, map[string]string{
				"errorCode": "API_ERROR", "errorMessage": "empty sort is not omission",
			})
			return
		}
	}
	pageNumber, pageErr := strconv.Atoi(query.Get("page"))
	pageSize, sizeErr := strconv.Atoi(query.Get("size"))
	if pageErr != nil || sizeErr != nil || pageNumber < 0 || pageSize < 1 {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"errorCode": "API_ERROR", "errorMessage": "invalid pageable values",
		})
		return
	}

	totalPages := (len(collection) + pageSize - 1) / pageSize
	start := pageNumber * pageSize
	if start > len(collection) {
		start = len(collection)
	}
	end := start + pageSize
	if end > len(collection) {
		end = len(collection)
	}
	content := append([]agentGroup(nil), collection[start:end]...)
	envelope := []page{{
		Content: content, Empty: len(content) == 0, First: pageNumber == 0,
		Last: pageNumber+1 >= totalPages, Number: pageNumber,
		NumberOfElements: len(content), Size: pageSize,
		TotalElements: len(collection), TotalPages: totalPages,
	}}
	writeJSON(w, http.StatusOK, envelope)
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
