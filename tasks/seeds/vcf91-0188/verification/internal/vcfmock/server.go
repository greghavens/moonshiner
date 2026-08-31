// Package vcfmock provides a loopback-only Log Management mock whose routing
// table is loaded from the checked-in OpenAPI contract.
package vcfmock

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"sync/atomic"
)

// RequestRecord is a copy of an HTTP request observed by the mock.
type RequestRecord struct {
	Method     string
	RequestURI string
	Header     http.Header
	Body       []byte
}

type operation struct {
	OperationID string `json:"operationId"`
}

type contractDocument struct {
	Info struct {
		Version string `json:"version"`
	} `json:"info"`
	Paths map[string]map[string]operation `json:"paths"`
}

// Server serves only method/path pairs named by its contract. Responses are
// consumed in request order, while Requests returns the separately recorded
// wire traffic for assertions.
type Server struct {
	server    *httptest.Server
	baseURL   string
	client    *http.Client
	allowed   map[string]string
	responses []json.RawMessage

	mu       sync.Mutex
	requests []RequestRecord
	next     int
}

var unixSocketSequence uint64

type roundTripFunc func(*http.Request) (*http.Response, error)

func (fn roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return fn(request)
}

// New starts a contract-pinned loopback server.
func New(contractPath string, responses []json.RawMessage) (*Server, error) {
	content, err := os.ReadFile(contractPath)
	if err != nil {
		return nil, fmt.Errorf("read contract: %w", err)
	}

	var document contractDocument
	if err := json.Unmarshal(content, &document); err != nil {
		return nil, fmt.Errorf("decode contract: %w", err)
	}
	if document.Info.Version != "9.1.0.0" {
		return nil, fmt.Errorf("unexpected contract version %q", document.Info.Version)
	}

	allowed := make(map[string]string)
	for path, methods := range document.Paths {
		for method, op := range methods {
			if op.OperationID == "" {
				return nil, fmt.Errorf("contract operation %s %s has no operationId", method, path)
			}
			allowed[strings.ToUpper(method)+" "+path] = op.OperationID
		}
	}
	if len(allowed) != 1 || allowed["POST /api/v2/logs/search"] != "executeLogSearchQuery_1" {
		return nil, fmt.Errorf("contract operations do not match the pinned fixture: %v", allowed)
	}

	s := &Server{allowed: allowed, responses: cloneResponses(responses)}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		// Some restricted CI sandboxes deny AF_INET even inside an isolated
		// network namespace. Keep a real HTTP server in that environment by
		// falling back to a Linux abstract Unix socket. The client still uses a
		// loopback-only base URL and cannot reach an external host.
		socketName := fmt.Sprintf("@vcfmock-%d-%d", os.Getpid(), atomic.AddUint64(&unixSocketSequence, 1))
		listener, err = net.Listen("unix", socketName)
		if err != nil {
			// The authoring sandbox can prohibit socket creation entirely. Keep
			// the same net/http request/handler boundary in that environment so
			// the wire recorder and contract routing remain authoritative.
			s.baseURL = "http://127.0.0.1"
			s.client = &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
				recorder := httptest.NewRecorder()
				s.serveHTTP(recorder, request)
				return recorder.Result(), nil
			})}
			return s, nil
		}
		transport := &http.Transport{
			DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
				var dialer net.Dialer
				return dialer.DialContext(ctx, "unix", socketName)
			},
		}
		s.client = &http.Client{Transport: transport}
		s.baseURL = "http://127.0.0.1"
	}
	s.server = httptest.NewUnstartedServer(http.HandlerFunc(s.serveHTTP))
	s.server.Listener = listener
	s.server.Start()
	if s.client == nil {
		s.client = s.server.Client()
		s.baseURL = s.server.URL
	}
	return s, nil
}

func cloneResponses(source []json.RawMessage) []json.RawMessage {
	result := make([]json.RawMessage, len(source))
	for index := range source {
		result[index] = append(json.RawMessage(nil), source[index]...)
	}
	return result
}

func (s *Server) serveHTTP(writer http.ResponseWriter, request *http.Request) {
	body, err := io.ReadAll(io.LimitReader(request.Body, 1<<20))
	if err != nil {
		http.Error(writer, "cannot read request", http.StatusBadRequest)
		return
	}

	s.mu.Lock()
	s.requests = append(s.requests, RequestRecord{
		Method:     request.Method,
		RequestURI: request.URL.RequestURI(),
		Header:     request.Header.Clone(),
		Body:       append([]byte(nil), body...),
	})
	_, ok := s.allowed[request.Method+" "+request.URL.Path]
	if !ok {
		s.mu.Unlock()
		http.NotFound(writer, request)
		return
	}
	if request.URL.RawQuery != "" {
		s.mu.Unlock()
		http.Error(writer, "query parameters are not part of this contract", http.StatusBadRequest)
		return
	}
	if s.next >= len(s.responses) {
		s.mu.Unlock()
		http.Error(writer, "no fixture response remains", http.StatusInternalServerError)
		return
	}
	response := append(json.RawMessage(nil), s.responses[s.next]...)
	s.next++
	s.mu.Unlock()

	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(http.StatusOK)
	_, _ = writer.Write(response)
}

// URL returns the loopback server's base URL.
func (s *Server) URL() string { return s.baseURL }

// Client returns an HTTP client configured for this server.
func (s *Server) Client() *http.Client { return s.client }

// Requests returns a deep copy of the request log.
func (s *Server) Requests() []RequestRecord {
	s.mu.Lock()
	defer s.mu.Unlock()
	result := make([]RequestRecord, len(s.requests))
	for index, request := range s.requests {
		result[index] = RequestRecord{
			Method:     request.Method,
			RequestURI: request.RequestURI,
			Header:     request.Header.Clone(),
			Body:       append([]byte(nil), request.Body...),
		}
	}
	return result
}

// Close stops the loopback server.
func (s *Server) Close() error {
	if s == nil {
		return errors.New("mock server is not running")
	}
	if s.server != nil {
		s.server.Close()
	}
	if s.client != nil {
		s.client.CloseIdleConnections()
	}
	return nil
}
