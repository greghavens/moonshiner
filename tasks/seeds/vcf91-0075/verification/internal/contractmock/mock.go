// Package contractmock provides a loopback-only server for the focused contract
// in docs/contract.json. It deliberately has no generic or catch-all API route.
package contractmock

import (
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"regexp"
	"sync"
)

// Route is one operation served by the mock.
type Route struct {
	OperationID string
	Method      string
	Path        string
}

var contractRoutes = []Route{
	{OperationID: "ListGroupForDomain", Method: http.MethodGet, Path: "/policy/api/v1/infra/domains/{domain-id}/groups"},
	{OperationID: "UpdateGroupForDomain", Method: http.MethodPut, Path: "/policy/api/v1/infra/domains/{domain-id}/groups/{group-id}"},
}

var routePatterns = []*regexp.Regexp{
	regexp.MustCompile(`^/policy/api/v1/infra/domains/[^/]+/groups$`),
	regexp.MustCompile(`^/policy/api/v1/infra/domains/[^/]+/groups/[^/]+$`),
}

// ContractRoutes returns a copy of the exact operations served by the mock.
func ContractRoutes() []Route {
	return append([]Route(nil), contractRoutes...)
}

// Request is an immutable request-log entry passed to a Responder.
type Request struct {
	OperationID string
	Method      string
	RequestURI  string
	Header      http.Header
	Body        []byte
}

// Reply controls one mock response.
type Reply struct {
	Status int
	Header http.Header
	Body   string
}

// Responder returns a response for a request already matched to the contract.
type Responder func(Request) Reply

// Server wraps an httptest server and its concurrency-safe request log.
type Server struct {
	responder Responder
	mu        sync.Mutex
	requests  []Request
	wg        sync.WaitGroup
}

// New starts a loopback server. With a nil responder, list returns an empty
// result and update echoes its request body; neither behavior stores state.
func New(responder Responder) *Server {
	return &Server{responder: responder}
}

// URL returns a loopback-only base URL. Client routes requests to the handler
// in-process, so no socket is opened and no network destination is reachable.
func (s *Server) URL() string { return "http://127.0.0.1" }

// Client returns an HTTP client whose transport serves only the focused
// contract. It never delegates to a network transport.
func (s *Server) Client() *http.Client {
	return &http.Client{Transport: roundTripper{server: s}}
}

// Close waits for any handler that outlived a canceled client request.
func (s *Server) Close() { s.wg.Wait() }

// Requests returns a deep copy of the request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.requests))
	for i, request := range s.requests {
		out[i] = cloneRequest(request)
	}
	return out
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	operationID := ""
	for i, route := range contractRoutes {
		if r.Method == route.Method && routePatterns[i].MatchString(r.URL.EscapedPath()) {
			operationID = route.OperationID
			break
		}
	}
	if operationID == "" {
		http.NotFound(w, r)
		return
	}

	var body []byte
	if r.Body != nil {
		var err error
		body, err = io.ReadAll(r.Body)
		if err != nil {
			http.Error(w, "could not read body", http.StatusBadRequest)
			return
		}
	}
	request := Request{
		OperationID: operationID,
		Method:      r.Method,
		RequestURI:  r.RequestURI,
		Header:      r.Header.Clone(),
		Body:        append([]byte(nil), body...),
	}
	s.mu.Lock()
	s.requests = append(s.requests, cloneRequest(request))
	s.mu.Unlock()

	reply := Reply{Status: http.StatusOK, Header: make(http.Header)}
	if s.responder != nil {
		reply = s.responder(cloneRequest(request))
	} else if operationID == "ListGroupForDomain" {
		reply.Body = `{"results":[]}`
	} else {
		reply.Body = string(body)
	}
	if reply.Status == 0 {
		reply.Status = http.StatusOK
	}
	for key, values := range reply.Header {
		for _, value := range values {
			w.Header().Add(key, value)
		}
	}
	if w.Header().Get("Content-Type") == "" {
		w.Header().Set("Content-Type", "application/json")
	}
	w.WriteHeader(reply.Status)
	_, _ = io.WriteString(w, reply.Body)
}

func cloneRequest(request Request) Request {
	request.Header = request.Header.Clone()
	request.Body = append([]byte(nil), request.Body...)
	return request
}

type roundTripper struct{ server *Server }

func (transport roundTripper) RoundTrip(request *http.Request) (*http.Response, error) {
	if request.URL.Scheme != "http" || request.URL.Host != "127.0.0.1" {
		return nil, errors.New("contract mock refuses non-loopback destination")
	}
	recorder := httptest.NewRecorder()
	request = request.Clone(request.Context())
	request.RequestURI = request.URL.RequestURI()
	done := make(chan struct{})
	transport.server.wg.Add(1)
	go func() {
		defer transport.server.wg.Done()
		transport.server.serveHTTP(recorder, request)
		close(done)
	}()
	select {
	case <-request.Context().Done():
		return nil, request.Context().Err()
	case <-done:
		return recorder.Result(), nil
	}
}
