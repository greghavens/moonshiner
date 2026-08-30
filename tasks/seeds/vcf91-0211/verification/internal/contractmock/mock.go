// Package contractmock provides a loopback HTTP server whose callable surface
// is loaded from the protected, reduced OpenAPI contract.
package contractmock

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"
)

// Request is a captured HTTP request. Requests returns deep copies of these
// records so tests can inspect the log without racing the server.
type Request struct {
	Method           string
	RequestURI       string
	Path             string
	Header           http.Header
	Body             []byte
	ContentLength    int64
	TransferEncoding []string
}

// Response describes one local mock response.
type Response struct {
	Status      int
	ContentType string
	Body        []byte
}

// Responder returns a response for a request matching the sole contract route.
type Responder func(Request) Response

// Server is an IPv4 loopback-only, contract-pinned mock.
type Server struct {
	server      *httptest.Server
	method      string
	path        string
	operationID string
	responder   Responder

	mu       sync.Mutex
	requests []Request
}

type contractDocument struct {
	Paths map[string]map[string]struct {
		OperationID string `json:"operationId"`
	} `json:"paths"`
}

// New loads the only callable operation from contractPath and starts a server
// on an ephemeral 127.0.0.1 port.
func New(t testing.TB, contractPath string, responder Responder) *Server {
	t.Helper()

	data, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var contract contractDocument
	if err := json.Unmarshal(data, &contract); err != nil {
		t.Fatalf("decode contract: %v", err)
	}

	type route struct {
		method      string
		path        string
		operationID string
	}
	var routes []route
	allowedMethods := map[string]bool{
		"delete": true, "get": true, "patch": true, "post": true, "put": true,
	}
	for path, item := range contract.Paths {
		for method, operation := range item {
			method = strings.ToLower(method)
			if allowedMethods[method] && operation.OperationID != "" {
				routes = append(routes, route{
					method:      strings.ToUpper(method),
					path:        path,
					operationID: operation.OperationID,
				})
			}
		}
	}
	sort.Slice(routes, func(i, j int) bool {
		return routes[i].path+routes[i].method < routes[j].path+routes[j].method
	})
	if len(routes) != 1 {
		t.Fatalf("contract mock requires exactly one callable operation, got %d", len(routes))
	}

	s := &Server{
		method:      routes[0].method,
		path:        routes[0].path,
		operationID: routes[0].operationID,
		responder:   responder,
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen on IPv4 loopback: %v", err)
	}
	s.server = &httptest.Server{
		Listener: listener,
		Config:   &http.Server{Handler: http.HandlerFunc(s.serveHTTP)},
	}
	s.server.Start()
	t.Cleanup(s.server.Close)
	return s
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		http.Error(w, "cannot read request", http.StatusBadRequest)
		return
	}
	record := Request{
		Method:           r.Method,
		RequestURI:       r.RequestURI,
		Path:             r.URL.Path,
		Header:           r.Header.Clone(),
		Body:             append([]byte(nil), body...),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
	}
	s.mu.Lock()
	s.requests = append(s.requests, record)
	s.mu.Unlock()

	if r.Method != s.method || r.URL.Path != s.path {
		http.NotFound(w, r)
		return
	}
	if s.responder == nil {
		http.Error(w, "no responder", http.StatusInternalServerError)
		return
	}
	response := s.responder(record)
	if response.Status == 0 {
		response.Status = http.StatusOK
	}
	if response.ContentType != "" {
		w.Header().Set("Content-Type", response.ContentType)
	}
	w.WriteHeader(response.Status)
	if len(response.Body) != 0 {
		_, _ = w.Write(response.Body)
	}
}

// URL returns the server's HTTP origin.
func (s *Server) URL() string { return s.server.URL }

// OperationID returns the operation loaded from the contract.
func (s *Server) OperationID() string { return s.operationID }

// Route returns the method and path loaded from the contract.
func (s *Server) Route() (string, string) { return s.method, s.path }

// Requests returns a synchronized deep copy of the request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	result := make([]Request, len(s.requests))
	for i, request := range s.requests {
		result[i] = request
		result[i].Header = request.Header.Clone()
		result[i].Body = append([]byte(nil), request.Body...)
		result[i].TransferEncoding = append([]string(nil), request.TransferEncoding...)
	}
	return result
}

// JSONResponse is a test helper for producing a compact JSON response.
func JSONResponse(t testing.TB, status int, value any) Response {
	t.Helper()
	body, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("marshal mock response: %v", err)
	}
	return Response{Status: status, ContentType: "application/json", Body: body}
}

// PageNumber reads the optional zero-based pageNumber query member.
func PageNumber(request Request) (int, error) {
	parsed, err := http.NewRequest(request.Method, "http://loopback"+request.RequestURI, nil)
	if err != nil {
		return 0, err
	}
	values := parsed.URL.Query()["pageNumber"]
	if len(values) == 0 {
		return 0, nil
	}
	if len(values) != 1 {
		return 0, fmt.Errorf("pageNumber occurs %d times", len(values))
	}
	page, err := strconv.Atoi(values[0])
	if err != nil {
		return 0, fmt.Errorf("invalid pageNumber %q", values[0])
	}
	return page, nil
}
