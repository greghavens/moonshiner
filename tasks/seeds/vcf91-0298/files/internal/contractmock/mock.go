// Package contractmock provides an IPv4 loopback HTTP server whose entire
// callable surface is loaded from the protected, reduced OpenAPI contract in
// docs/contract.json. It serves no operation that the contract does not name and
// records every request it receives, including rejected ones.
package contractmock

import (
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"sort"
	"strings"
	"sync"
	"testing"
)

// Request is a captured HTTP request. Requests returns deep copies of these
// records so tests can inspect the log without racing the server.
type Request struct {
	OperationID      string
	Method           string
	RequestURI       string
	Path             string
	PathParams       map[string]string
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

// Responder produces a response for a request that matched a contract route.
type Responder func(Request) Response

// Route is one callable operation loaded from the contract.
type Route struct {
	OperationID string
	Method      string
	Path        string
	segments    []string
}

// Server is a contract-pinned loopback mock.
type Server struct {
	server    *httptest.Server
	basePath  string
	routes    []Route
	responder Responder

	mu       sync.Mutex
	requests []Request
}

type contractDocument struct {
	Servers []struct {
		URL string `json:"url"`
	} `json:"servers"`
	Paths map[string]map[string]struct {
		OperationID string `json:"operationId"`
	} `json:"paths"`
}

var httpMethods = map[string]string{
	"delete": http.MethodDelete,
	"get":    http.MethodGet,
	"head":   http.MethodHead,
	"patch":  http.MethodPatch,
	"post":   http.MethodPost,
	"put":    http.MethodPut,
}

// New loads every callable operation from contractPath and starts a server on an
// ephemeral 127.0.0.1 port. Routes are mounted beneath the contract's declared
// service root.
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
	if len(contract.Servers) != 1 {
		t.Fatalf("contract declares %d service roots, want 1", len(contract.Servers))
	}
	basePath := strings.TrimSuffix(contract.Servers[0].URL, "/")
	if basePath == "" || !strings.HasPrefix(basePath, "/") {
		t.Fatalf("contract service root %q is not an absolute path", contract.Servers[0].URL)
	}

	var routes []Route
	for path, item := range contract.Paths {
		for method, operation := range item {
			canonical, ok := httpMethods[strings.ToLower(method)]
			if !ok || operation.OperationID == "" {
				continue
			}
			routes = append(routes, Route{
				OperationID: operation.OperationID,
				Method:      canonical,
				Path:        path,
				segments:    strings.Split(strings.TrimPrefix(path, "/"), "/"),
			})
		}
	}
	if len(routes) == 0 {
		t.Fatalf("contract names no callable operation")
	}
	sort.Slice(routes, func(i, j int) bool {
		if routes[i].Path != routes[j].Path {
			return routes[i].Path < routes[j].Path
		}
		return routes[i].Method < routes[j].Method
	})

	s := &Server{basePath: basePath, routes: routes, responder: responder}
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

// match resolves a concrete request path to a contract route. Literal segments
// are compared exactly; a "{name}" segment binds any single non-empty segment.
func (s *Server) match(method, path string) (Route, map[string]string, bool) {
	rest, ok := strings.CutPrefix(path, s.basePath)
	if !ok || (rest != "" && !strings.HasPrefix(rest, "/")) {
		return Route{}, nil, false
	}
	actual := strings.Split(strings.TrimPrefix(rest, "/"), "/")
	for _, route := range s.routes {
		if route.Method != method || len(route.segments) != len(actual) {
			continue
		}
		params := map[string]string{}
		matched := true
		for i, want := range route.segments {
			got := actual[i]
			if strings.HasPrefix(want, "{") && strings.HasSuffix(want, "}") {
				if got == "" {
					matched = false
					break
				}
				params[strings.Trim(want, "{}")] = got
				continue
			}
			if want != got {
				matched = false
				break
			}
		}
		if matched {
			return route, params, true
		}
	}
	return Route{}, nil, false
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		http.Error(w, "cannot read request", http.StatusBadRequest)
		return
	}
	route, params, ok := s.match(r.Method, r.URL.Path)
	record := Request{
		OperationID:      route.OperationID,
		Method:           r.Method,
		RequestURI:       r.RequestURI,
		Path:             r.URL.Path,
		PathParams:       params,
		Header:           r.Header.Clone(),
		Body:             append([]byte(nil), body...),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
	}
	s.mu.Lock()
	s.requests = append(s.requests, record)
	s.mu.Unlock()

	if !ok {
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

// URL returns the server's HTTP origin, without the contract service root.
func (s *Server) URL() string { return s.server.URL }

// BasePath returns the service root loaded from the contract.
func (s *Server) BasePath() string { return s.basePath }

// Routes returns the callable operations loaded from the contract, ordered by
// path then method.
func (s *Server) Routes() []Route {
	result := make([]Route, len(s.routes))
	copy(result, s.routes)
	return result
}

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
		params := make(map[string]string, len(request.PathParams))
		for k, v := range request.PathParams {
			params[k] = v
		}
		result[i].PathParams = params
	}
	return result
}

// OperationIDs returns the operationId of every logged request in order. A
// request that matched no contract route contributes an empty string.
func (s *Server) OperationIDs() []string {
	requests := s.Requests()
	ids := make([]string, len(requests))
	for i, request := range requests {
		ids[i] = request.OperationID
	}
	return ids
}

// JSONResponse is a helper for producing a compact JSON response.
func JSONResponse(t testing.TB, status int, value any) Response {
	t.Helper()
	body, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("marshal mock response: %v", err)
	}
	return Response{Status: status, ContentType: "application/json", Body: body}
}
