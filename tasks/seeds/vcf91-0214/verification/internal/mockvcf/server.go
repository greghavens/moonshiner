// Package mockvcf provides a loopback-only mock for the two operations pinned
// in docs/contract.json. It intentionally returns 404 for every other method or
// path and exposes a race-safe copy of its request log to acceptance tests.
package mockvcf

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"sync"
)

type Operation struct {
	OperationID string
	Method      string
	Path        string
}

var pinnedOperations = []Operation{
	{OperationID: "validateSddcSpec", Method: http.MethodPost, Path: "/v1/sddcs/validations"},
	{OperationID: "deploySddc", Method: http.MethodPost, Path: "/v1/sddcs"},
}

func AllowedOperations() []Operation {
	return append([]Operation(nil), pinnedOperations...)
}

type ValidationResponse struct {
	ID              string `json:"id"`
	Description     string `json:"description"`
	ExecutionStatus string `json:"executionStatus"`
	ResultStatus    string `json:"resultStatus"`
}

type DeployResponse struct {
	ID                string `json:"id"`
	Status            string `json:"status"`
	CreationTimestamp string `json:"creationTimestamp"`
}

type Scenario struct {
	ValidationStatus  int
	Validation        ValidationResponse
	ValidationRawBody []byte
	DeployStatus      int
	Deployment        DeployResponse
	DeploymentRawBody []byte
}

type Request struct {
	Method     string
	RequestURI string
	Header     http.Header
	Body       []byte
}

type Server struct {
	server   *httptest.Server
	client   *http.Client
	scenario Scenario

	mu        sync.Mutex
	requests  []Request
	mutations int
}

func New(scenario Scenario) *Server {
	s := &Server{scenario: withDefaults(scenario)}
	listener := newLoopbackListener()
	s.server = &httptest.Server{
		Listener: listener,
		Config:   &http.Server{Handler: http.HandlerFunc(s.serveHTTP)},
	}
	s.server.Start()
	s.client = &http.Client{Transport: &http.Transport{
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			return listener.dial(ctx)
		},
	}}
	return s
}

func withDefaults(s Scenario) Scenario {
	if s.ValidationStatus == 0 {
		s.ValidationStatus = http.StatusOK
	}
	if s.DeployStatus == 0 {
		s.DeployStatus = http.StatusAccepted
	}
	if s.Validation.ID == "" {
		s.Validation = ValidationResponse{
			ID:              "validation-001",
			Description:     "VCF Installer specification validation",
			ExecutionStatus: "COMPLETED",
			ResultStatus:    "SUCCEEDED",
		}
	}
	if s.Deployment.ID == "" {
		s.Deployment = DeployResponse{
			ID:                "sfo01-m01",
			Status:            "IN_PROGRESS",
			CreationTimestamp: "2026-01-15T12:00:00Z",
		}
	}
	return s
}

func (s *Server) URL() string {
	return s.server.URL
}

func (s *Server) Client() *http.Client {
	return s.client
}

func (s *Server) Close() {
	s.client.CloseIdleConnections()
	s.server.Close()
}

func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()

	result := make([]Request, len(s.requests))
	for i, request := range s.requests {
		result[i] = Request{
			Method:     request.Method,
			RequestURI: request.RequestURI,
			Header:     request.Header.Clone(),
			Body:       append([]byte(nil), request.Body...),
		}
	}
	return result
}

func (s *Server) Mutations() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.mutations
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "unable to read request", http.StatusBadRequest)
		return
	}

	s.mu.Lock()
	s.requests = append(s.requests, Request{
		Method:     r.Method,
		RequestURI: r.URL.RequestURI(),
		Header:     r.Header.Clone(),
		Body:       append([]byte(nil), body...),
	})
	s.mu.Unlock()

	switch {
	case r.Method == http.MethodPost && r.URL.Path == "/v1/sddcs/validations" && r.URL.RawQuery == "":
		if s.scenario.ValidationRawBody != nil {
			writeRawJSON(w, s.scenario.ValidationStatus, s.scenario.ValidationRawBody)
			return
		}
		writeJSON(w, s.scenario.ValidationStatus, s.scenario.Validation)
	case r.Method == http.MethodPost && r.URL.Path == "/v1/sddcs" && r.URL.RawQuery == "":
		s.mu.Lock()
		s.mutations++
		s.mu.Unlock()
		if s.scenario.DeploymentRawBody != nil {
			writeRawJSON(w, s.scenario.DeployStatus, s.scenario.DeploymentRawBody)
			return
		}
		writeJSON(w, s.scenario.DeployStatus, s.scenario.Deployment)
	default:
		writeJSON(w, http.StatusNotFound, map[string]string{"message": "operation is not present in the pinned contract"})
	}
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeRawJSON(w http.ResponseWriter, status int, body []byte) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

// loopbackListener gives httptest.Server a loopback address while carrying
// connections over net.Pipe. This preserves the complete net/http wire path in
// sandboxes that prohibit bind(2), and cannot route to any non-local endpoint.
type loopbackListener struct {
	connections chan net.Conn
	closed      chan struct{}
	closeOnce   sync.Once
}

func newLoopbackListener() *loopbackListener {
	return &loopbackListener{
		connections: make(chan net.Conn),
		closed:      make(chan struct{}),
	}
}

func (l *loopbackListener) Accept() (net.Conn, error) {
	select {
	case connection := <-l.connections:
		return connection, nil
	case <-l.closed:
		return nil, net.ErrClosed
	}
}

func (l *loopbackListener) Close() error {
	l.closeOnce.Do(func() { close(l.closed) })
	return nil
}

func (l *loopbackListener) Addr() net.Addr {
	return &net.TCPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0}
}

func (l *loopbackListener) dial(ctx context.Context) (net.Conn, error) {
	client, server := net.Pipe()
	select {
	case l.connections <- server:
		return client, nil
	case <-ctx.Done():
		_ = client.Close()
		_ = server.Close()
		return nil, ctx.Err()
	case <-l.closed:
		_ = client.Close()
		_ = server.Close()
		return nil, net.ErrClosed
	}
}
