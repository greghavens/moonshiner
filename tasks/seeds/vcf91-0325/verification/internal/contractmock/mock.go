// Package contractmock provides an IPv4 loopback HTTP server whose callable
// surface is pinned to the protected, reference-derived docs/contract.json.
//
// The server serves only the operations that the contract names. Every other
// method or path is a 404. Every query parameter that the contract does not
// name for the matched operation is a 400, as is any parameter that is sent
// with an empty value or sent more than once, because the contract describes
// all of them as optional and an optional parameter that is not set must be
// absent from the wire rather than present and empty.
//
// No live VMware endpoint is contacted. The server binds 127.0.0.1:0.
package contractmock

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"sort"
	"strings"
	"sync"
	"testing"
)

// Request is a captured HTTP request. Requests returns deep copies of these
// records so tests can read the log without racing the server.
type Request struct {
	Method           string
	RequestURI       string
	Path             string
	RawQuery         string
	Query            url.Values
	Header           http.Header
	Body             []byte
	ContentLength    int64
	TransferEncoding []string
}

// QueryKeys returns the sorted set of query parameter names on the request.
func (r Request) QueryKeys() []string {
	keys := make([]string, 0, len(r.Query))
	for key := range r.Query {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

// Response describes one mock response.
type Response struct {
	Status      int
	ContentType string
	Body        []byte
}

// Responder produces a response for a request that matched a contract route.
// The operation name of the matched route is passed through so a single
// responder can serve every contract operation.
type Responder func(operationName string, request Request) Response

// Server is a contract-pinned loopback mock.
type Server struct {
	server *httptest.Server
	routes []route

	mu       sync.Mutex
	requests []Request

	responder Responder
}

type route struct {
	operationName string
	method        string
	path          string
	queryNames    map[string]bool
}

type contractDocument struct {
	SourceBasis struct {
		Kind                     string `json:"kind"`
		IsPublishedSpecification bool   `json:"isPublishedSpecification"`
		Statement                string `json:"statement"`
	} `json:"sourceBasis"`
	Security struct {
		HeaderName  string `json:"headerName"`
		ValuePrefix string `json:"valuePrefix"`
	} `json:"security"`
	Operations []struct {
		OperationName   string `json:"operationName"`
		Method          string `json:"method"`
		Path            string `json:"path"`
		Produces        []string
		QueryParameters []struct {
			Name     string `json:"name"`
			Required bool   `json:"required"`
		} `json:"queryParameters"`
	} `json:"operations"`
}

// New loads the callable operations from contractPath and starts a server on
// an ephemeral 127.0.0.1 port. The server is closed during test cleanup.
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
	if len(contract.Operations) == 0 {
		t.Fatal("contract names no operations")
	}

	s := &Server{responder: responder}
	for _, operation := range contract.Operations {
		if operation.OperationName == "" || operation.Method == "" || operation.Path == "" {
			t.Fatalf("contract operation is incomplete: %+v", operation)
		}
		names := make(map[string]bool, len(operation.QueryParameters))
		for _, parameter := range operation.QueryParameters {
			names[parameter.Name] = true
		}
		s.routes = append(s.routes, route{
			operationName: operation.OperationName,
			method:        strings.ToUpper(operation.Method),
			path:          operation.Path,
			queryNames:    names,
		})
	}
	sort.Slice(s.routes, func(i, j int) bool {
		if s.routes[i].path != s.routes[j].path {
			return s.routes[i].path < s.routes[j].path
		}
		return s.routes[i].method < s.routes[j].method
	})

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
		writePlain(w, http.StatusBadRequest, "cannot read request")
		return
	}
	query, queryErr := url.ParseQuery(r.URL.RawQuery)
	record := Request{
		Method:           r.Method,
		RequestURI:       r.RequestURI,
		Path:             r.URL.Path,
		RawQuery:         r.URL.RawQuery,
		Query:            query,
		Header:           r.Header.Clone(),
		Body:             append([]byte(nil), body...),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
	}
	s.mu.Lock()
	s.requests = append(s.requests, record)
	s.mu.Unlock()

	if queryErr != nil {
		writePlain(w, http.StatusBadRequest, "malformed query string")
		return
	}

	matched, pathExists := s.match(r.Method, r.URL.Path)
	if !pathExists {
		writePlain(w, http.StatusNotFound, "no operation in the contract serves "+r.URL.Path)
		return
	}
	if matched == nil {
		writePlain(w, http.StatusMethodNotAllowed, "the contract does not document "+r.Method+" on "+r.URL.Path)
		return
	}

	for name, values := range query {
		if !matched.queryNames[name] {
			writePlain(w, http.StatusBadRequest, fmt.Sprintf("query parameter %q is not documented for %q", name, matched.operationName))
			return
		}
		if len(values) != 1 {
			writePlain(w, http.StatusBadRequest, fmt.Sprintf("query parameter %q was sent %d times", name, len(values)))
			return
		}
		if values[0] == "" {
			writePlain(w, http.StatusBadRequest, fmt.Sprintf("optional query parameter %q was sent with an empty value; unset optional parameters must be omitted", name))
			return
		}
	}

	if !strings.Contains(r.Header.Get("Accept"), "application/json") {
		writePlain(w, http.StatusNotAcceptable, "operation produces application/json")
		return
	}
	authorization := r.Header.Get("Authorization")
	if !strings.HasPrefix(authorization, "Bearer ") || strings.TrimSpace(strings.TrimPrefix(authorization, "Bearer ")) == "" {
		writePlain(w, http.StatusUnauthorized, "bearerAuth credentials are required")
		return
	}

	if s.responder == nil {
		writePlain(w, http.StatusInternalServerError, "no responder")
		return
	}
	response := s.responder(matched.operationName, record)
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

// match reports the route for method and path. The second result reports
// whether any contract route serves the path at all.
func (s *Server) match(method, path string) (*route, bool) {
	pathExists := false
	for i := range s.routes {
		if s.routes[i].path != path {
			continue
		}
		pathExists = true
		if s.routes[i].method == strings.ToUpper(method) {
			return &s.routes[i], true
		}
	}
	return nil, pathExists
}

func writePlain(w http.ResponseWriter, status int, message string) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.WriteHeader(status)
	_, _ = io.WriteString(w, message+"\n")
}

// URL returns the server's HTTP origin.
func (s *Server) URL() string { return s.server.URL }

// Operations returns the operation names loaded from the contract, sorted.
func (s *Server) Operations() []string {
	names := make([]string, 0, len(s.routes))
	for _, r := range s.routes {
		names = append(names, r.operationName)
	}
	sort.Strings(names)
	return names
}

// Route returns the method and path pinned for operationName.
func (s *Server) Route(operationName string) (string, string, bool) {
	for _, r := range s.routes {
		if r.operationName == operationName {
			return r.method, r.path, true
		}
	}
	return "", "", false
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
		result[i].Query = url.Values{}
		for key, values := range request.Query {
			result[i].Query[key] = append([]string(nil), values...)
		}
	}
	return result
}

// JSONResponse marshals value into a compact application/json response.
func JSONResponse(t testing.TB, status int, value any) Response {
	t.Helper()
	body, err := json.Marshal(value)
	if err != nil {
		t.Fatalf("marshal mock response: %v", err)
	}
	return Response{Status: status, ContentType: "application/json", Body: body}
}
