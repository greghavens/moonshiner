// Package mockvcf provides a contract-pinned loopback server for client tests.
package mockvcf

import (
	"encoding/json"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"

	"vcfnetworks"
)

// Request is an immutable snapshot of one request received by Server.
type Request struct {
	Method     string
	RequestURI string
	Header     http.Header
	Body       []byte
}

// Server is a race-safe loopback implementation of the embedded operation.
type Server struct {
	server *httptest.Server

	contract  vcfnetworks.Contract
	operation vcfnetworks.ContractOperation
	success   vcfnetworks.ContractResponse
	failure   vcfnetworks.ContractResponse
	failFirst bool

	mu            sync.Mutex
	requests      []Request
	validRequests int
	state         string
	effectCount   int
}

// NewServer starts a loopback mock pinned to docs/contract.json. When failFirst
// is true, the first valid update is applied before the server responds with 500.
func NewServer(failFirst bool) *Server {
	contract, err := vcfnetworks.LoadContract()
	if err != nil {
		panic(fmt.Sprintf("load embedded VCF Networks contract: %v", err))
	}
	if len(contract.Operations) != 1 {
		panic(fmt.Sprintf("VCF Networks mock requires exactly one contract operation, got %d", len(contract.Operations)))
	}
	operation := contract.Operations[0]
	var success, failure vcfnetworks.ContractResponse
	for _, response := range operation.Responses {
		switch response.Status {
		case http.StatusOK:
			success = response
		case http.StatusInternalServerError:
			failure = response
		}
	}
	if success.Status == 0 || failure.Status == 0 {
		panic("VCF Networks mock contract must declare HTTP 200 and HTTP 500")
	}
	server := &Server{
		contract:  contract,
		operation: operation,
		success:   success,
		failure:   failure,
		failFirst: failFirst,
	}
	server.server = httptest.NewServer(http.HandlerFunc(server.serveHTTP))
	return server
}

// URL returns the loopback server base URL.
func (s *Server) URL() string {
	return s.server.URL
}

// Close stops the loopback server.
func (s *Server) Close() {
	s.server.Close()
}

// Requests returns defensive copies of all request records.
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

// EffectCount reports how many distinct representations changed server state.
func (s *Server) EffectCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.effectCount
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "read request", http.StatusBadRequest)
		return
	}
	s.record(r, body)

	id, ok := s.match(r)
	if !ok {
		http.NotFound(w, r)
		return
	}
	authorization := r.Header.Get(s.operation.Security.Name)
	if !strings.HasPrefix(authorization, s.operation.Security.ValuePrefix) || strings.TrimPrefix(authorization, s.operation.Security.ValuePrefix) == "" {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}
	mediaType, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if err != nil || mediaType != s.operation.RequestBody.ContentType {
		http.Error(w, "content type must match contract", http.StatusBadRequest)
		return
	}

	var update vcfnetworks.VCenterUpdate
	decoder := json.NewDecoder(strings.NewReader(string(body)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&update); err != nil {
		http.Error(w, "invalid update body", http.StatusBadRequest)
		return
	}

	s.mu.Lock()
	s.validRequests++
	validRequestNumber := s.validRequests
	if s.state != string(body) {
		s.state = string(body)
		s.effectCount++
	}
	s.mu.Unlock()

	if s.failFirst && validRequestNumber == 1 {
		if s.failure.ContentType != "" {
			w.Header().Set("Content-Type", s.failure.ContentType)
		}
		w.WriteHeader(s.failure.Status)
		return
	}

	if s.success.ContentType != "" {
		w.Header().Set("Content-Type", s.success.ContentType)
	}
	w.WriteHeader(s.success.Status)
	_ = json.NewEncoder(w).Encode(vcfnetworks.VCenterDataSource{
		EntityID:    id,
		Nickname:    update.Nickname,
		Notes:       update.Notes,
		Credentials: update.Credentials,
	})
}

func (s *Server) record(r *http.Request, body []byte) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, Request{
		Method:     r.Method,
		RequestURI: r.RequestURI,
		Header:     r.Header.Clone(),
		Body:       append([]byte(nil), body...),
	})
}

func (s *Server) match(r *http.Request) (string, bool) {
	if r.Method != s.operation.Method || r.URL.RawQuery != "" {
		return "", false
	}
	template := s.contract.BasePath + s.operation.Path
	prefix := strings.TrimSuffix(template, "{id}")
	if prefix == template {
		return "", false
	}
	escapedPath := r.URL.EscapedPath()
	if !strings.HasPrefix(escapedPath, prefix) {
		return "", false
	}
	escapedID := strings.TrimPrefix(escapedPath, prefix)
	if escapedID == "" || strings.Contains(escapedID, "/") {
		return "", false
	}
	id, err := url.PathUnescape(escapedID)
	return id, err == nil
}
