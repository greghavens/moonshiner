// Package contractmock provides the loopback-only VCF Installer fixture.
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
	"strings"
	"sync"
	"testing"
)

const (
	pinnedCommit = "c3f3b52c845dd967cabbc21680e893292077d5ba"
	pinnedPath   = "specifications/vcf-installer/vcf-installer-openapi.json"
)

type contractDocument struct {
	Source struct {
		RepositoryCommitSHA string `json:"repositoryCommitSha"`
		SpecPath            string `json:"specPath"`
	} `json:"source"`
	Operations []operation `json:"operations"`
}

type operation struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

// Request is a lossless-enough server-side record for wire verification.
type Request struct {
	OperationID      string
	Method           string
	RawTarget        string
	Header           http.Header
	Body             []byte
	ContentLength    int64
	TransferEncoding []string
}

// Server is a contract-pinned, loopback-only VCF Installer mock.
type Server struct {
	httpServer *httptest.Server
	routes     map[string]operation
	statuses   []string
	taskID     string

	mu       sync.Mutex
	requests []Request
	poll     int
}

// Start loads the focused contract and starts a loopback server.
func Start(t testing.TB, contractPath string, statuses []string) *Server {
	t.Helper()
	data, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read focused contract: %v", err)
	}
	var document contractDocument
	if err := json.Unmarshal(data, &document); err != nil {
		t.Fatalf("decode focused contract: %v", err)
	}
	if document.Source.RepositoryCommitSHA != pinnedCommit || document.Source.SpecPath != pinnedPath {
		t.Fatalf("focused contract is not pinned to the VCF Installer 9.1 source")
	}
	want := []operation{
		{OperationID: "updateProxyConfiguration", Method: http.MethodPatch, Path: "/v1/system/proxy-configuration"},
		{OperationID: "getTask", Method: http.MethodGet, Path: "/v1/tasks/{id}"},
	}
	if len(document.Operations) != len(want) {
		t.Fatalf("focused contract has %d operations, want %d", len(document.Operations), len(want))
	}
	routes := make(map[string]operation, len(want))
	for i := range want {
		got := document.Operations[i]
		if got != want[i] {
			t.Fatalf("focused operation %d = %+v, want %+v", i, got, want[i])
		}
		routes[got.OperationID] = got
	}
	if len(statuses) == 0 {
		t.Fatal("mock requires at least one polled status")
	}

	s := &Server{
		routes:   routes,
		statuses: append([]string(nil), statuses...),
		taskID:   "task/91 proxy?0209",
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen on loopback: %v", err)
	}
	s.httpServer = &httptest.Server{
		Listener: listener,
		Config:   &http.Server{Handler: http.HandlerFunc(s.serveHTTP)},
	}
	s.httpServer.Start()
	parsed, err := url.Parse(s.httpServer.URL)
	if err != nil {
		s.httpServer.Close()
		t.Fatalf("parse mock URL: %v", err)
	}
	host, _, err := net.SplitHostPort(parsed.Host)
	if err != nil || host != "127.0.0.1" || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
		s.httpServer.Close()
		t.Fatalf("mock did not bind to loopback: %q", parsed.Host)
	}
	t.Cleanup(s.Close)
	return s
}

// URL returns the loopback service root.
func (s *Server) URL() string { return s.httpServer.URL }

// TaskID returns the asynchronous task identifier used by the fixture.
func (s *Server) TaskID() string { return s.taskID }

// Close stops the mock.
func (s *Server) Close() { s.httpServer.Close() }

// Requests returns a deep copy of the synchronized request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	result := make([]Request, len(s.requests))
	for i, request := range s.requests {
		result[i] = request
		result[i].Header = request.Header.Clone()
		result[i].Body = append([]byte(nil), request.Body...)
		result[i].TransferEncoding = append([]string(nil), request.TransferEncoding...)
	}
	return result
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	operationID := s.match(r.Method, r.URL.EscapedPath())
	s.record(Request{
		OperationID:      operationID,
		Method:           r.Method,
		RawTarget:        r.RequestURI,
		Header:           r.Header.Clone(),
		Body:             append([]byte(nil), body...),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
	})

	switch operationID {
	case "updateProxyConfiguration":
		s.writeTask(w, http.StatusAccepted, "PENDING")
	case "getTask":
		s.mu.Lock()
		index := s.poll
		if index >= len(s.statuses) {
			index = len(s.statuses) - 1
		}
		status := s.statuses[index]
		s.poll++
		s.mu.Unlock()
		s.writeTask(w, http.StatusOK, status)
	default:
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		_, _ = io.WriteString(w, `{"errorCode":"NOT_IN_CONTRACT","message":"operation is outside the focused contract"}`)
	}
}

func (s *Server) match(method, escapedPath string) string {
	update := s.routes["updateProxyConfiguration"]
	if method == update.Method && escapedPath == update.Path {
		return update.OperationID
	}
	lookup := s.routes["getTask"]
	prefix := strings.TrimSuffix(lookup.Path, "{id}")
	if method == lookup.Method && escapedPath == prefix+url.PathEscape(s.taskID) {
		return lookup.OperationID
	}
	return ""
}

func (s *Server) record(request Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, request)
}

func (s *Server) writeTask(w http.ResponseWriter, statusCode int, status string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"id":                s.taskID,
		"name":              "Update proxy configuration",
		"status":            status,
		"creationTimestamp": "2026-08-02T12:00:00Z",
	})
}

// String makes unexpected route records compact in test failures.
func (r Request) String() string {
	return fmt.Sprintf("%s %s (%s)", r.Method, r.RawTarget, r.OperationID)
}
