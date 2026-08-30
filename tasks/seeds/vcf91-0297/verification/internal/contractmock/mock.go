// Package contractmock provides an IPv4 loopback HTTP server whose callable
// surface is loaded from the protected, reduced OpenAPI contract in
// docs/contract.json. It serves no operation the contract does not name and
// records every request it receives so tests can assert the exact wire shape.
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
// records so tests can inspect the log without racing the server.
type Request struct {
	// OperationID is the contract operation this request matched, or "" when
	// the request addressed a route absent from the contract.
	OperationID string
	// PathParams holds the templated path parameters the route captured.
	PathParams map[string]string

	Method           string
	RequestURI       string
	Path             string
	RawQuery         string
	Header           http.Header
	Body             []byte
	ContentLength    int64
	TransferEncoding []string
}

// Query parses the request's raw query string.
func (r Request) Query() (url.Values, error) { return url.ParseQuery(r.RawQuery) }

// Response describes one local mock response.
type Response struct {
	Status      int
	ContentType string
	Body        []byte
}

// Responder produces a response for a request that matched a contract route.
type Responder func(Request) Response

type route struct {
	operationID string
	method      string
	path        string   // contract path, e.g. /entities/problems/{id}
	segments    []string // basePath + path, split
	params      map[string]bool
}

// Server is an IPv4 loopback-only, contract-pinned mock.
type Server struct {
	server     *httptest.Server
	basePath   string
	authName   string
	authPrefix string
	routes     []route
	responders map[string]Responder
	mu         sync.Mutex
	requests   []Request
}

type contractDocument struct {
	BasePath string `json:"basePath"`
	Security struct {
		Name        string `json:"name"`
		In          string `json:"in"`
		ValuePrefix string `json:"valuePrefix"`
	} `json:"security"`
	Paths map[string]map[string]struct {
		OperationID string `json:"operationId"`
		Parameters  []struct {
			Name string `json:"name"`
			In   string `json:"in"`
		} `json:"parameters"`
	} `json:"paths"`
}

// New loads every callable operation from contractPath and starts a server on
// an ephemeral 127.0.0.1 port. responders is keyed by operationId and must
// cover exactly the operations the contract names.
func New(t testing.TB, contractPath string, responders map[string]Responder) *Server {
	t.Helper()

	data, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var contract contractDocument
	if err := json.Unmarshal(data, &contract); err != nil {
		t.Fatalf("decode contract: %v", err)
	}
	if contract.BasePath == "" {
		t.Fatal("contract does not declare a basePath")
	}
	if contract.Security.In != "header" || contract.Security.Name == "" {
		t.Fatalf("contract does not declare a header security scheme: %+v", contract.Security)
	}

	allowed := map[string]bool{
		"delete": true, "get": true, "patch": true, "post": true, "put": true,
	}
	s := &Server{
		basePath:   contract.BasePath,
		authName:   contract.Security.Name,
		authPrefix: contract.Security.ValuePrefix,
		responders: map[string]Responder{},
	}
	for path, item := range contract.Paths {
		for method, operation := range item {
			if !allowed[strings.ToLower(method)] || operation.OperationID == "" {
				continue
			}
			params := map[string]bool{}
			for _, p := range operation.Parameters {
				if p.In == "query" {
					params[p.Name] = true
				}
			}
			s.routes = append(s.routes, route{
				operationID: operation.OperationID,
				method:      strings.ToUpper(method),
				path:        path,
				segments:    splitPath(contract.BasePath + path),
				params:      params,
			})
		}
	}
	if len(s.routes) == 0 {
		t.Fatal("contract names no callable operation")
	}
	// Deterministic order, and literal routes are matched before templated ones.
	sort.Slice(s.routes, func(i, j int) bool {
		li, lj := templateCount(s.routes[i].segments), templateCount(s.routes[j].segments)
		if li != lj {
			return li < lj
		}
		return s.routes[i].path+s.routes[i].method < s.routes[j].path+s.routes[j].method
	})

	named := map[string]bool{}
	for _, r := range s.routes {
		named[r.operationID] = true
	}
	for id, responder := range responders {
		if !named[id] {
			t.Fatalf("responder %q is not an operation the contract names", id)
		}
		s.responders[id] = responder
	}
	for id := range named {
		if s.responders[id] == nil {
			t.Fatalf("no responder supplied for contract operation %q", id)
		}
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

func splitPath(p string) []string {
	return strings.Split(strings.Trim(p, "/"), "/")
}

func templateCount(segments []string) int {
	n := 0
	for _, s := range segments {
		if strings.HasPrefix(s, "{") && strings.HasSuffix(s, "}") {
			n++
		}
	}
	return n
}

func (r route) match(method string, path string) (map[string]string, bool) {
	if method != r.method {
		return nil, false
	}
	got := splitPath(path)
	if len(got) != len(r.segments) {
		return nil, false
	}
	params := map[string]string{}
	for i, want := range r.segments {
		if strings.HasPrefix(want, "{") && strings.HasSuffix(want, "}") {
			if got[i] == "" {
				return nil, false
			}
			params[strings.Trim(want, "{}")] = got[i]
			continue
		}
		if got[i] != want {
			return nil, false
		}
	}
	return params, true
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
		RawQuery:         r.URL.RawQuery,
		Header:           r.Header.Clone(),
		Body:             append([]byte(nil), body...),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
	}

	var matched *route
	for i := range s.routes {
		if params, ok := s.routes[i].match(r.Method, r.URL.Path); ok {
			matched = &s.routes[i]
			record.OperationID = s.routes[i].operationID
			record.PathParams = params
			break
		}
	}

	s.mu.Lock()
	s.requests = append(s.requests, record)
	s.mu.Unlock()

	if matched == nil {
		s.writeError(w, http.StatusNotFound, "no such operation in contract")
		return
	}
	if s.authPrefix != "" {
		value := r.Header.Get(s.authName)
		if !strings.HasPrefix(value, s.authPrefix) || strings.TrimPrefix(value, s.authPrefix) == "" {
			s.writeError(w, http.StatusUnauthorized, "missing or malformed credentials")
			return
		}
	}
	query, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil {
		s.writeError(w, http.StatusBadRequest, "malformed query string")
		return
	}
	for name := range query {
		if !matched.params[name] {
			s.writeError(w, http.StatusBadRequest,
				fmt.Sprintf("query parameter %q is not declared for operation %s", name, matched.operationID))
			return
		}
	}

	response := s.responders[matched.operationID](record)
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

func (s *Server) writeError(w http.ResponseWriter, status int, message string) {
	body, _ := json.Marshal(map[string]any{"code": status, "message": message})
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

// URL returns the server's HTTP origin.
func (s *Server) URL() string { return s.server.URL }

// Client returns an HTTP client configured for this server.
func (s *Server) Client() *http.Client { return s.server.Client() }

// BasePath returns the service root loaded from the contract.
func (s *Server) BasePath() string { return s.basePath }

// OperationIDs returns the sorted operations the contract names.
func (s *Server) OperationIDs() []string {
	ids := make([]string, 0, len(s.routes))
	for _, r := range s.routes {
		ids = append(ids, r.operationID)
	}
	sort.Strings(ids)
	return ids
}

// Route returns the method and full request path template for an operation.
func (s *Server) Route(operationID string) (string, string, bool) {
	for _, r := range s.routes {
		if r.operationID == operationID {
			return r.method, s.basePath + r.path, true
		}
	}
	return "", "", false
}

// Requests returns a synchronized deep copy of the whole request log.
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

// RequestsFor returns the logged requests that matched one contract operation,
// in arrival order.
func (s *Server) RequestsFor(operationID string) []Request {
	var result []Request
	for _, request := range s.Requests() {
		if request.OperationID == operationID {
			result = append(result, request)
		}
	}
	return result
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
