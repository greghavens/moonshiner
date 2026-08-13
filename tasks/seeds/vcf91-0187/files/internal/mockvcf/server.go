// Package mockvcf provides a loopback-only mock of the focused contract.
package mockvcf

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"sync"
	"time"
)

const (
	CreateAgentSecretOperation  = "createAgentSecret"
	CreateAgentSessionOperation = "createAgentSession"

	CreateAgentSecretPath  = "/api/v2/agent/secrets"
	CreateAgentSessionPath = "/api/v2/agent/secrets/exchange"
)

// Operation is one route implemented by Server.
type Operation struct {
	OperationID string
	Method      string
	Path        string
}

// Operations returns the complete set of operations served by this mock.
func Operations() []Operation {
	return []Operation{
		{OperationID: CreateAgentSecretOperation, Method: http.MethodPost, Path: CreateAgentSecretPath},
		{OperationID: CreateAgentSessionOperation, Method: http.MethodPost, Path: CreateAgentSessionPath},
	}
}

// ExchangeResult describes one configured createAgentSession response.
type ExchangeResult struct {
	StatusCode   int
	AccessToken  string
	Name         string
	NewSecret    string
	TTL          int64
	ErrorCode    string
	ErrorMessage string
}

// Config supplies all deterministic state used by a Server.
type Config struct {
	AdminToken         string
	InitialAgentSecret string
	CreatedID          string
	CreatedSecret      string
	CreatedStatus      string
	BlockFirstExchange bool
	ExchangeResults    []ExchangeResult
}

// RequestRecord is the exact request observed by the loopback server.
type RequestRecord struct {
	OperationID      string
	Method           string
	Path             string
	RawQuery         string
	Proto            string
	Host             string
	Header           http.Header
	Body             []byte
	ContentLength    int64
	TransferEncoding []string
}

// Server is an httptest-backed server that implements only Operations.
type Server struct {
	config   Config
	server   *http.Server
	client   *http.Client
	listener *pipeListener

	requestsMu sync.Mutex
	requests   []RequestRecord

	exchangeMu      sync.Mutex
	activeSecret    string
	exchangeRequest int

	firstStarted chan struct{}
	releaseFirst chan struct{}
	startOnce    sync.Once
	releaseOnce  sync.Once
}

// New starts a loopback server using config.
func New(config Config) *Server {
	listener := newPipeListener()
	s := &Server{
		config:       config,
		activeSecret: config.InitialAgentSecret,
		firstStarted: make(chan struct{}),
		releaseFirst: make(chan struct{}),
		listener:     listener,
	}
	s.server = &http.Server{
		Handler:           http.HandlerFunc(s.serveHTTP),
		ReadHeaderTimeout: time.Second,
	}
	transport := &http.Transport{
		DialContext: listener.DialContext,
	}
	s.client = &http.Client{Transport: transport, Timeout: 5 * time.Second}
	go func() {
		if err := s.server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			panic(err)
		}
	}()
	return s
}

// URL returns the loopback base URL.
func (s *Server) URL() string { return "http://127.0.0.1" }

// Client returns the server's HTTP client.
func (s *Server) Client() *http.Client { return s.client }

// FirstExchangeStarted closes after the first exchange request is logged.
func (s *Server) FirstExchangeStarted() <-chan struct{} { return s.firstStarted }

// ReleaseFirstExchange releases a configured first-exchange barrier.
func (s *Server) ReleaseFirstExchange() {
	s.releaseOnce.Do(func() { close(s.releaseFirst) })
}

// Requests returns a deep copy of the request log.
func (s *Server) Requests() []RequestRecord {
	s.requestsMu.Lock()
	defer s.requestsMu.Unlock()
	out := make([]RequestRecord, len(s.requests))
	for i, request := range s.requests {
		out[i] = request
		out[i].Header = request.Header.Clone()
		out[i].Body = append([]byte(nil), request.Body...)
		out[i].TransferEncoding = append([]string(nil), request.TransferEncoding...)
	}
	return out
}

// Close stops the server and releases any barrier first.
func (s *Server) Close() {
	s.ReleaseFirstExchange()
	if transport, ok := s.client.Transport.(*http.Transport); ok {
		transport.CloseIdleConnections()
	}
	_ = s.server.Close()
	_ = s.listener.Close()
}

func (s *Server) serveHTTP(response http.ResponseWriter, request *http.Request) {
	operationID, knownPath := route(request.Method, request.URL.Path)
	body, readErr := io.ReadAll(io.LimitReader(request.Body, 1<<20))
	_ = request.Body.Close()
	s.record(RequestRecord{
		OperationID:      operationID,
		Method:           request.Method,
		Path:             request.URL.Path,
		RawQuery:         request.URL.RawQuery,
		Proto:            request.Proto,
		Host:             request.Host,
		Header:           request.Header.Clone(),
		Body:             body,
		ContentLength:    request.ContentLength,
		TransferEncoding: append([]string(nil), request.TransferEncoding...),
	})
	if readErr != nil {
		writeError(response, http.StatusBadRequest, "JSON_FORMAT_ERROR", readErr.Error())
		return
	}
	if operationID == "" {
		if knownPath {
			response.Header().Set("Allow", http.MethodPost)
			writeError(response, http.StatusMethodNotAllowed, "API_ERROR", "method not allowed")
			return
		}
		writeError(response, http.StatusNotFound, "API_ERROR", "operation not served")
		return
	}
	if request.Header.Get("X-JWT-Token") != s.config.AdminToken {
		writeError(response, http.StatusForbidden, "SECURITY_ERROR", "wrong administrator token")
		return
	}
	if request.Header.Get("Content-Type") != "application/json" {
		writeError(response, http.StatusBadRequest, "JSON_FORMAT_ERROR", "content type must be application/json")
		return
	}

	switch operationID {
	case CreateAgentSecretOperation:
		s.createAgentSecret(response, body)
	case CreateAgentSessionOperation:
		s.createAgentSession(response, request, body)
	default:
		panic("unreachable operation: " + operationID)
	}
}

func route(method, path string) (operationID string, knownPath bool) {
	switch path {
	case CreateAgentSecretPath:
		if method == http.MethodPost {
			return CreateAgentSecretOperation, true
		}
		return "", true
	case CreateAgentSessionPath:
		if method == http.MethodPost {
			return CreateAgentSessionOperation, true
		}
		return "", true
	default:
		return "", false
	}
}

func (s *Server) record(request RequestRecord) {
	s.requestsMu.Lock()
	s.requests = append(s.requests, request)
	s.requestsMu.Unlock()
}

func (s *Server) createAgentSecret(response http.ResponseWriter, body []byte) {
	var input struct {
		Name *string `json:"name"`
	}
	if err := decodeOne(body, &input); err != nil {
		writeError(response, http.StatusBadRequest, "JSON_FORMAT_ERROR", err.Error())
		return
	}
	name := ""
	if input.Name != nil {
		name = *input.Name
	}
	writeJSON(response, http.StatusCreated, map[string]any{
		"id": s.config.CreatedID, "name": name, "secret": s.config.CreatedSecret,
		"status": s.config.CreatedStatus,
	})
}

func (s *Server) createAgentSession(response http.ResponseWriter, request *http.Request, body []byte) {
	var input struct {
		Secret string `json:"secret"`
		TTL    *int64 `json:"ttl"`
	}
	if err := decodeOne(body, &input); err != nil {
		writeError(response, http.StatusBadRequest, "JSON_FORMAT_ERROR", err.Error())
		return
	}

	s.exchangeMu.Lock()
	defer s.exchangeMu.Unlock()
	index := s.exchangeRequest
	s.exchangeRequest++
	if index == 0 {
		s.startOnce.Do(func() { close(s.firstStarted) })
		if s.config.BlockFirstExchange {
			select {
			case <-s.releaseFirst:
			case <-request.Context().Done():
				return
			}
		}
	}
	if input.Secret != s.activeSecret {
		writeError(response, http.StatusBadRequest, "AGENT_ERROR", "stale agent secret")
		return
	}
	if index >= len(s.config.ExchangeResults) {
		writeError(response, http.StatusInternalServerError, "INTERNAL_SERVER_ERROR", "no exchange result configured")
		return
	}
	result := s.config.ExchangeResults[index]
	if result.StatusCode < 200 || result.StatusCode >= 300 {
		writeError(response, result.StatusCode, result.ErrorCode, result.ErrorMessage)
		return
	}
	s.activeSecret = result.NewSecret
	writeJSON(response, result.StatusCode, map[string]any{
		"access_token": result.AccessToken,
		"name":         result.Name,
		"new_secret":   result.NewSecret,
		"ttl":          result.TTL,
	})
}

func decodeOne(body []byte, target any) error {
	if len(body) == 0 {
		return fmt.Errorf("empty JSON body")
	}
	if err := json.Unmarshal(body, target); err != nil {
		return err
	}
	return nil
}

func writeError(response http.ResponseWriter, status int, code, message string) {
	writeJSON(response, status, map[string]any{
		"errorCode": code, "errorDetails": map[string]any{}, "errorMessage": message,
	})
}

func writeJSON(response http.ResponseWriter, status int, value any) {
	response.Header().Set("Content-Type", "application/json")
	response.WriteHeader(status)
	_ = json.NewEncoder(response).Encode(value)
}

// pipeListener lets the ordinary net/http client and server exchange real
// HTTP/1.1 bytes without opening a socket. Addr deliberately identifies the
// endpoint as loopback, matching Server.URL.
type pipeListener struct {
	connections chan net.Conn
	done        chan struct{}
	closeOnce   sync.Once
}

func newPipeListener() *pipeListener {
	return &pipeListener{
		connections: make(chan net.Conn),
		done:        make(chan struct{}),
	}
}

func (l *pipeListener) Accept() (net.Conn, error) {
	select {
	case connection := <-l.connections:
		return connection, nil
	case <-l.done:
		return nil, net.ErrClosed
	}
}

func (l *pipeListener) Close() error {
	l.closeOnce.Do(func() { close(l.done) })
	return nil
}

func (l *pipeListener) Addr() net.Addr { return loopbackAddr{} }

func (l *pipeListener) DialContext(ctx context.Context, _, _ string) (net.Conn, error) {
	serverConnection, clientConnection := net.Pipe()
	select {
	case l.connections <- serverConnection:
		return clientConnection, nil
	case <-ctx.Done():
		_ = serverConnection.Close()
		_ = clientConnection.Close()
		return nil, ctx.Err()
	case <-l.done:
		_ = serverConnection.Close()
		_ = clientConnection.Close()
		return nil, net.ErrClosed
	}
}

type loopbackAddr struct{}

func (loopbackAddr) Network() string { return "tcp" }
func (loopbackAddr) String() string  { return "127.0.0.1:80" }
