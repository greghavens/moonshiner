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

// Request is a server-side record used for exact wire verification.
type Request struct {
	OperationID      string
	Method           string
	RawTarget        string
	Header           http.Header
	Body             []byte
	ContentLength    int64
	TransferEncoding []string
}

// Server is a contract-pinned, loopback-only mock. It can drop the first
// response after applying the semantic delete, creating an ambiguous result.
type Server struct {
	httpServer *httptest.Server
	route      operation
	dropFirst  bool

	mu       sync.Mutex
	requests []Request
	effects  map[string]struct{}
}

// Start loads the focused contract and starts an ephemeral IPv4 loopback server.
func Start(t testing.TB, contractPath string, dropFirst bool) *Server {
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
		t.Fatal("focused contract is not pinned to the VCF Installer 9.1 source")
	}
	want := operation{
		OperationID: "deleteDepotSettings",
		Method:      http.MethodDelete,
		Path:        "/v1/system/settings/depot",
	}
	if len(document.Operations) != 1 || document.Operations[0] != want {
		t.Fatalf("focused operations = %+v, want only %+v", document.Operations, want)
	}

	s := &Server{route: want, dropFirst: dropFirst, effects: make(map[string]struct{})}
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

// Close stops the mock.
func (s *Server) Close() { s.httpServer.Close() }

// Requests returns a deep copy of the synchronized request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	result := make([]Request, len(s.requests))
	for index, request := range s.requests {
		result[index] = request
		result[index].Header = request.Header.Clone()
		result[index].Body = append([]byte(nil), request.Body...)
		result[index].TransferEncoding = append([]string(nil), request.TransferEncoding...)
	}
	return result
}

// EffectCount reports distinct semantic mutations applied by the mock.
func (s *Server) EffectCount() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.effects)
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	operationID := ""
	if r.Method == s.route.Method && r.URL.EscapedPath() == s.route.Path {
		operationID = s.route.OperationID
	}
	record := Request{
		OperationID:      operationID,
		Method:           r.Method,
		RawTarget:        r.RequestURI,
		Header:           r.Header.Clone(),
		Body:             append([]byte(nil), body...),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
	}

	s.mu.Lock()
	s.requests = append(s.requests, record)
	requestNumber := len(s.requests)
	if operationID != "" {
		s.effects[r.RequestURI] = struct{}{}
	}
	drop := operationID != "" && s.dropFirst && requestNumber == 1
	s.mu.Unlock()

	if operationID == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		_, _ = io.WriteString(w, `{"errorCode":"NOT_IN_CONTRACT","message":"operation is outside the focused contract"}`)
		return
	}
	if drop {
		hijacker, ok := w.(http.Hijacker)
		if !ok {
			panic("loopback response writer does not support hijacking")
		}
		connection, _, err := hijacker.Hijack()
		if err != nil {
			panic(fmt.Sprintf("hijack first response: %v", err))
		}
		_ = connection.Close()
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// String makes unexpected route records compact in failures.
func (r Request) String() string {
	return fmt.Sprintf("%s %s (%s)", r.Method, r.RawTarget, r.OperationID)
}
