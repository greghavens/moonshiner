// Package contractmock provides an IPv4 loopback HTTP server whose callable
// surface is loaded from the protected, reference-derived contract in
// docs/contract.json. The server routes only the operations that contract
// names; every other method and path is rejected without reaching a responder.
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
	"time"
)

// Request is a captured HTTP request. Requests returns deep copies of these
// records so tests can inspect the log without racing the server.
type Request struct {
	// OperationID is the contract operation this request matched, or "" when
	// the request matched no contract route.
	OperationID string
	// PathParams holds the contract path-template variables, for example
	// "deploymentId", captured from the escaped request path.
	PathParams map[string]string
	ReceivedAt time.Time

	Method           string
	RequestURI       string
	Path             string
	RawQuery         string
	Header           http.Header
	Body             []byte
	ContentLength    int64
	TransferEncoding []string
}

// Response describes one local mock response.
type Response struct {
	Status                int
	ContentType           string
	Body                  []byte
	WaitForRequestContext bool
}

// Responder returns a response for a request that matched a contract route.
type Responder func(Request) Response

// JSON builds a Response carrying v encoded as application/json.
func JSON(status int, v any) Response {
	body, err := json.Marshal(v)
	if err != nil {
		panic(fmt.Sprintf("contractmock: marshal response: %v", err))
	}
	return Response{Status: status, ContentType: "application/json", Body: body}
}

type route struct {
	operationID string
	method      string
	// segments is the split path template; a segment of the form "{name}" is a
	// single-segment variable.
	segments []string
}

// Server is an IPv4 loopback-only, contract-pinned mock.
type Server struct {
	server    *httptest.Server
	routes    []route
	responder Responder

	mu       sync.Mutex
	requests []Request
}

type contractDocument struct {
	Paths map[string]map[string]struct {
		OperationID string `json:"operationId"`
	} `json:"paths"`
}

var contractMethods = map[string]bool{
	"delete": true, "get": true, "head": true, "options": true,
	"patch": true, "post": true, "put": true,
}

// New loads every callable operation from contractPath and starts a server on
// an ephemeral 127.0.0.1 port. The server is closed when the test finishes.
func New(t testing.TB, contractPath string, responder Responder) *Server {
	t.Helper()

	data, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("contractmock: read contract: %v", err)
	}
	var contract contractDocument
	if err := json.Unmarshal(data, &contract); err != nil {
		t.Fatalf("contractmock: decode contract: %v", err)
	}

	var routes []route
	for template, item := range contract.Paths {
		for method, operation := range item {
			method = strings.ToLower(method)
			if !contractMethods[method] || operation.OperationID == "" {
				continue
			}
			if !strings.HasPrefix(template, "/") {
				t.Fatalf("contractmock: contract path %q is not absolute", template)
			}
			routes = append(routes, route{
				operationID: operation.OperationID,
				method:      strings.ToUpper(method),
				segments:    strings.Split(strings.TrimPrefix(template, "/"), "/"),
			})
		}
	}
	if len(routes) == 0 {
		t.Fatal("contractmock: contract names no callable operation")
	}
	sort.Slice(routes, func(i, j int) bool {
		if a, b := strings.Join(routes[i].segments, "/"), strings.Join(routes[j].segments, "/"); a != b {
			return a < b
		}
		return routes[i].method < routes[j].method
	})

	s := &Server{routes: routes, responder: responder}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("contractmock: listen on IPv4 loopback: %v", err)
	}
	s.server = &httptest.Server{
		Listener: listener,
		Config:   &http.Server{Handler: http.HandlerFunc(s.serveHTTP)},
	}
	s.server.Start()
	t.Cleanup(s.server.Close)
	return s
}

// URL is the service root, for example "http://127.0.0.1:39481".
func (s *Server) URL() string { return s.server.URL }

// Requests returns deep copies of every request the server received, including
// requests that matched no contract route.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()

	out := make([]Request, 0, len(s.requests))
	for _, r := range s.requests {
		copied := r
		copied.Header = r.Header.Clone()
		copied.Body = append([]byte(nil), r.Body...)
		copied.TransferEncoding = append([]string(nil), r.TransferEncoding...)
		copied.PathParams = make(map[string]string, len(r.PathParams))
		for k, v := range r.PathParams {
			copied.PathParams[k] = v
		}
		out = append(out, copied)
	}
	return out
}

// Operations returns the contract operation of each logged request in order.
// Requests that matched no contract route appear as "".
func (s *Server) Operations() []string {
	requests := s.Requests()
	out := make([]string, 0, len(requests))
	for _, r := range requests {
		out = append(out, r.OperationID)
	}
	return out
}

// Count returns how many logged requests matched the given contract operation.
func (s *Server) Count(operationID string) int {
	n := 0
	for _, got := range s.Operations() {
		if got == operationID {
			n++
		}
	}
	return n
}

// match resolves an escaped request path against the contract routes. It
// reports the matched route, the captured variables, and whether some route
// matched the path with a different method.
func (s *Server) match(method, escapedPath string) (route, map[string]string, bool, bool) {
	got := strings.Split(strings.TrimPrefix(escapedPath, "/"), "/")
	pathKnown := false
	for _, r := range s.routes {
		if len(r.segments) != len(got) {
			continue
		}
		params := map[string]string{}
		ok := true
		for i, want := range r.segments {
			if strings.HasPrefix(want, "{") && strings.HasSuffix(want, "}") {
				if got[i] == "" {
					ok = false
					break
				}
				unescaped, err := url.PathUnescape(got[i])
				if err != nil {
					ok = false
					break
				}
				params[strings.Trim(want, "{}")] = unescaped
				continue
			}
			if want != got[i] {
				ok = false
				break
			}
		}
		if !ok {
			continue
		}
		pathKnown = true
		if r.method == method {
			return r, params, true, true
		}
	}
	return route{}, nil, false, pathKnown
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		body = nil
	}
	_ = r.Body.Close()

	matched, params, ok, pathKnown := s.match(r.Method, r.URL.EscapedPath())

	logged := Request{
		OperationID:      matched.operationID,
		PathParams:       params,
		ReceivedAt:       time.Now(),
		Method:           r.Method,
		RequestURI:       r.RequestURI,
		Path:             r.URL.EscapedPath(),
		RawQuery:         r.URL.RawQuery,
		Header:           r.Header.Clone(),
		Body:             body,
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
	}
	if !ok {
		logged.PathParams = map[string]string{}
	}

	s.mu.Lock()
	s.requests = append(s.requests, logged)
	s.mu.Unlock()

	if !ok {
		status := http.StatusNotFound
		message := fmt.Sprintf("no contract operation for %s %s", r.Method, r.URL.EscapedPath())
		if pathKnown {
			status = http.StatusMethodNotAllowed
			message = fmt.Sprintf("contract path %s does not define method %s", r.URL.EscapedPath(), r.Method)
		}
		writeResponse(w, JSON(status, map[string]string{
			"errorCode": "CONTRACT_ROUTE_UNKNOWN",
			"message":   message,
		}))
		return
	}

	response := s.responder(logged)
	if response.WaitForRequestContext {
		<-r.Context().Done()
		return
	}
	writeResponse(w, response)
}

func writeResponse(w http.ResponseWriter, resp Response) {
	if resp.ContentType != "" {
		w.Header().Set("Content-Type", resp.ContentType)
	}
	if resp.Status == 0 {
		resp.Status = http.StatusOK
	}
	w.WriteHeader(resp.Status)
	if len(resp.Body) > 0 {
		_, _ = w.Write(resp.Body)
	}
}
