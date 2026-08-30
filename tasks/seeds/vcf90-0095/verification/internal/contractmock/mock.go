// Package contractmock provides a loopback-only VCF Operations for Logs mock.
// Its route table is loaded from docs/contract.json so it cannot silently grow
// beyond the operations selected for this task.
package contractmock

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"testing"
)

// Response is a configured mock response.
type Response struct {
	Status int
	Body   string
}

// Config supplies responses without building any initial service state into the
// mock. Updates are keyed by the decoded forwarder ID.
type Config struct {
	List    Response
	Updates map[string]Response
}

// Request is an immutable copy of a request observed by the mock.
type Request struct {
	Method   string
	Path     string
	RawQuery string
	Header   http.Header
	Body     []byte
}

// Server is a contract-pinned loopback HTTP server.
type Server struct {
	testingTB testing.TB
	server    *http.Server
	listener  net.Listener
	client    *http.Client
	url       string
	listPath  string
	putPrefix string
	config    Config

	mu       sync.Mutex
	requests []Request
}

type contractDocument struct {
	ServerBasePath string `json:"serverBasePath"`
	Operations     []struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	} `json:"operations"`
}

// New starts a loopback mock pinned to the supplied derived contract.
func New(t testing.TB, contractPath string, config Config) *Server {
	t.Helper()

	raw, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var contract contractDocument
	if err := json.Unmarshal(raw, &contract); err != nil {
		t.Fatalf("decode contract: %v", err)
	}

	var listPath, putPath string
	seen := make(map[string]bool)
	for _, operation := range contract.Operations {
		if seen[operation.OperationID] {
			t.Fatalf("duplicate contract operationId %q", operation.OperationID)
		}
		seen[operation.OperationID] = true
		switch operation.OperationID {
		case "GET_log-forwarder":
			if operation.Method != http.MethodGet || operation.Path != "/log-forwarder" {
				t.Fatalf("GET_log-forwarder route does not match pinned contract")
			}
			listPath = operation.Path
		case "PUT_log-forwarder-id":
			if operation.Method != http.MethodPut || operation.Path != "/log-forwarder/{id}" {
				t.Fatalf("PUT_log-forwarder-id route does not match pinned contract")
			}
			putPath = operation.Path
		default:
			t.Fatalf("contract mock refuses unselected operationId %q", operation.OperationID)
		}
	}
	if len(seen) != 2 || listPath == "" || putPath == "" || contract.ServerBasePath != "/api/v2" {
		t.Fatalf("contract must contain only the two pinned VCF Operations for Logs operations")
	}

	s := &Server{
		testingTB: t,
		listPath:  contract.ServerBasePath + listPath,
		putPrefix: contract.ServerBasePath + strings.TrimSuffix(putPath, "{id}"),
		config:    config,
	}
	s.server = &http.Server{Handler: http.HandlerFunc(s.serveHTTP)}
	transport := &http.Transport{}
	listener, listenErr := net.Listen("tcp", "127.0.0.1:0")
	if listenErr == nil {
		s.listener = listener
		s.url = "http://" + listener.Addr().String()
	} else {
		pipe := newPipeListener()
		s.listener = pipe
		s.url = "http://127.0.0.1"
		transport.DialContext = func(ctx context.Context, _, _ string) (net.Conn, error) {
			return pipe.DialContext(ctx)
		}
	}
	s.client = &http.Client{Transport: transport}
	go func() {
		if err := s.server.Serve(s.listener); err != nil && err != http.ErrServerClosed {
			t.Errorf("serve contract mock: %v", err)
		}
	}()
	t.Cleanup(func() {
		transport.CloseIdleConnections()
		_ = s.server.Close()
		_ = s.listener.Close()
	})
	return s
}

// URL returns the loopback appliance origin.
func (s *Server) URL() string { return s.url }

// Client returns the HTTP client associated with the loopback server.
func (s *Server) Client() *http.Client { return s.client }

// Requests returns deep copies of all requests observed so far.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()

	requests := make([]Request, len(s.requests))
	for i, request := range s.requests {
		requests[i] = request
		requests[i].Header = request.Header.Clone()
		requests[i].Body = append([]byte(nil), request.Body...)
	}
	return requests
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "could not read request", http.StatusBadRequest)
		return
	}

	s.mu.Lock()
	s.requests = append(s.requests, Request{
		Method:   r.Method,
		Path:     r.URL.EscapedPath(),
		RawQuery: r.URL.RawQuery,
		Header:   r.Header.Clone(),
		Body:     append([]byte(nil), body...),
	})
	s.mu.Unlock()

	if r.Method == http.MethodGet && r.URL.EscapedPath() == s.listPath {
		s.writeResponse(w, s.config.List)
		return
	}
	if r.Method == http.MethodPut && strings.HasPrefix(r.URL.EscapedPath(), s.putPrefix) {
		escapedID := strings.TrimPrefix(r.URL.EscapedPath(), s.putPrefix)
		id, unescapeErr := url.PathUnescape(escapedID)
		if unescapeErr == nil && id != "" {
			if response, ok := s.config.Updates[id]; ok {
				s.writeResponse(w, response)
				return
			}
		}
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusNotFound)
	_, _ = io.WriteString(w, `{"errorMessage":"operation is not in the pinned contract","errorCode":"FIELD_ERROR"}`)
}

func (s *Server) writeResponse(w http.ResponseWriter, response Response) {
	status := response.Status
	if status == 0 {
		status = http.StatusOK
	}
	if response.Body != "" {
		w.Header().Set("Content-Type", "application/json")
	}
	w.WriteHeader(status)
	if response.Body != "" {
		if _, err := io.WriteString(w, response.Body); err != nil {
			s.testingTB.Errorf("write mock response: %v", err)
		}
	}
}

// AssertLoopback reports whether the server URL remains loopback-only.
func (s *Server) AssertLoopback() error {
	parsed, err := url.Parse(s.url)
	if err != nil {
		return err
	}
	host := parsed.Hostname()
	if host != "127.0.0.1" && host != "::1" && host != "localhost" {
		return fmt.Errorf("mock is not loopback: %s", host)
	}
	return nil
}

type pipeListener struct {
	connections chan net.Conn
	closed      chan struct{}
	closeOnce   sync.Once
}

func newPipeListener() *pipeListener {
	return &pipeListener{
		connections: make(chan net.Conn),
		closed:      make(chan struct{}),
	}
}

func (l *pipeListener) Accept() (net.Conn, error) {
	select {
	case connection := <-l.connections:
		return connection, nil
	case <-l.closed:
		return nil, net.ErrClosed
	}
}

func (l *pipeListener) Close() error {
	l.closeOnce.Do(func() { close(l.closed) })
	return nil
}

func (l *pipeListener) Addr() net.Addr { return pipeAddress{} }

func (l *pipeListener) DialContext(ctx context.Context) (net.Conn, error) {
	client, server := net.Pipe()
	select {
	case l.connections <- server:
		return client, nil
	case <-l.closed:
		_ = client.Close()
		_ = server.Close()
		return nil, net.ErrClosed
	case <-ctx.Done():
		_ = client.Close()
		_ = server.Close()
		return nil, ctx.Err()
	}
}

type pipeAddress struct{}

func (pipeAddress) Network() string { return "pipe" }
func (pipeAddress) String() string  { return "127.0.0.1:80" }
