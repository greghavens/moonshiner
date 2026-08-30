// Package mocklogs provides a contract-pinned loopback test server.
package mocklogs

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"sort"
	"sync"

	contractdocs "example.com/vcfopslogs/docs"
)

const apiPrefix = "/api/v2"

// RequestLog is an immutable snapshot of an HTTP request received by Server.
type RequestLog struct {
	Method     string
	RequestURI string
	Header     http.Header
	Body       []byte
	// ContentLength is the request's declared wire length.
	ContentLength int64
}

// Server is a loopback VCF Operations for Logs server.
type Server struct {
	server *httptest.Server

	mu           sync.Mutex
	requests     []RequestLog
	waitStatuses []int
	waitIndex    int
}

type openAPIContract struct {
	Paths map[string]map[string]struct {
		OperationID string `json:"operationId"`
	} `json:"paths"`
}

// New starts a loopback server. waitStatuses scripts successive polling
// responses and may contain only the response codes named by the contract.
func New(waitStatuses ...int) (*Server, error) {
	if len(waitStatuses) == 0 {
		waitStatuses = []int{http.StatusOK}
	}
	for _, status := range waitStatuses {
		if status != http.StatusOK && status != http.StatusInternalServerError {
			return nil, fmt.Errorf("wait status %d is not in the contract", status)
		}
	}
	if err := validateContract(); err != nil {
		return nil, err
	}

	s := &Server{waitStatuses: append([]int(nil), waitStatuses...)}
	s.server = httptest.NewServer(http.HandlerFunc(s.handle))
	return s, nil
}

// URL returns the appliance root endpoint.
func (s *Server) URL() string { return s.server.URL }

// Client returns an HTTP client configured for the loopback server.
func (s *Server) Client() *http.Client { return s.server.Client() }

// Close stops the loopback server.
func (s *Server) Close() { s.server.Close() }

// Requests returns a deep copy of the request log.
func (s *Server) Requests() []RequestLog {
	s.mu.Lock()
	defer s.mu.Unlock()
	result := make([]RequestLog, len(s.requests))
	for i, request := range s.requests {
		result[i] = RequestLog{
			Method:        request.Method,
			RequestURI:    request.RequestURI,
			Header:        request.Header.Clone(),
			Body:          append([]byte(nil), request.Body...),
			ContentLength: request.ContentLength,
		}
	}
	return result
}

func (s *Server) handle(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	r.Body.Close()
	r.Body = io.NopCloser(bytes.NewReader(body))
	s.record(r, body)

	if r.Method != http.MethodPost {
		http.NotFound(w, r)
		return
	}
	switch r.URL.Path {
	case apiPrefix + "/deployment/join":
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"masterAddress": "10.0.0.123",
			"masterUiPort":  80,
			"workerAddress": "10.0.0.124",
			"workerPort":    16520,
			"workerToken":   "0ae94cb9-550a-4c01-85b9-3b7095e92321",
		})
	case apiPrefix + "/deployment/waitUntilStarted":
		status := s.nextWaitStatus()
		if status == http.StatusInternalServerError {
			w.Header().Set("Content-Type", "application/json")
		}
		w.WriteHeader(status)
		if status == http.StatusInternalServerError {
			io.WriteString(w, `{"errorMessage":"The operation failed due to an internal error."}`)
		}
	default:
		http.NotFound(w, r)
	}
}

func (s *Server) record(request *http.Request, body []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, RequestLog{
		Method:        request.Method,
		RequestURI:    request.URL.RequestURI(),
		Header:        request.Header.Clone(),
		Body:          append([]byte(nil), body...),
		ContentLength: request.ContentLength,
	})
}

func (s *Server) nextWaitStatus() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	index := s.waitIndex
	if index >= len(s.waitStatuses) {
		index = len(s.waitStatuses) - 1
	} else {
		s.waitIndex++
	}
	return s.waitStatuses[index]
}

func validateContract() error {
	var contract openAPIContract
	if err := json.Unmarshal(contractdocs.Contract, &contract); err != nil {
		return fmt.Errorf("parse embedded contract: %w", err)
	}
	var ids []string
	for _, methods := range contract.Paths {
		for _, operation := range methods {
			ids = append(ids, operation.OperationID)
		}
	}
	sort.Strings(ids)
	want := []string{"POST_deployment-join", "POST_deployment-waitUntilStarted"}
	if len(ids) != len(want) {
		return fmt.Errorf("contract operationIds = %v", ids)
	}
	for i := range want {
		if ids[i] != want[i] {
			return fmt.Errorf("contract operationIds = %v", ids)
		}
	}
	return nil
}
