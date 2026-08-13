// Package contractmock provides the contract-pinned loopback VCF Installer fixture.
package contractmock

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
	"testing"
)

const (
	pinnedCommit = "c3f3b52c845dd967cabbc21680e893292077d5ba"
	pinnedPath   = "specifications/vcf-installer/vcf-installer-openapi.json"
)

// Mode selects a protected authentication failure case.
type Mode int

const (
	// ExpireOnce makes page one return 401, then accepts one refresh and resumes.
	ExpireOnce Mode = iota
	// FailWith500 makes page one fail without an authentication challenge.
	FailWith500
	// SecondUnauthorized makes the retried page return a second 401.
	SecondUnauthorized
	// RefreshUnauthorized makes refreshAccessToken return 401.
	RefreshUnauthorized
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

// Task is a mock response record derived from the contract Task projection.
type Task struct {
	ID                string  `json:"id"`
	Name              string  `json:"name"`
	Type              *string `json:"type,omitempty"`
	Status            string  `json:"status"`
	CreationTimestamp string  `json:"creationTimestamp"`
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
	ResponseStatus   int
}

// Server is a focused loopback-only VCF Installer mock.
type Server struct {
	httpServer *httptest.Server
	routes     map[string]operation
	mode       Mode
	pageSize   int
	tasks      []Task
	oldToken   string
	newToken   string
	refreshID  string

	mu              sync.Mutex
	requests        []Request
	successfulPages []int
	challengeSent   bool
	refreshed       bool
}

// Start loads exactly the contract routes and starts an ephemeral loopback server.
func Start(t testing.TB, contractPath string, mode Mode) *Server {
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
		t.Fatal("focused contract is not pinned to the VCF Installer 9.1 specification")
	}
	want := []operation{
		{OperationID: "getTasks", Method: http.MethodGet, Path: "/v1/tasks"},
		{OperationID: "refreshAccessToken", Method: http.MethodPatch, Path: "/v1/tokens/access-token/refresh"},
	}
	if len(document.Operations) != len(want) {
		t.Fatalf("focused contract has %d operations, want %d", len(document.Operations), len(want))
	}
	routes := make(map[string]operation, len(want))
	for index, expected := range want {
		if document.Operations[index] != expected {
			t.Fatalf("focused operation %d = %+v, want %+v", index, document.Operations[index], expected)
		}
		routes[expected.OperationID] = expected
	}
	if mode < ExpireOnce || mode > RefreshUnauthorized {
		t.Fatalf("unsupported mock mode %d", mode)
	}

	digest := sha256.Sum256([]byte(t.Name()))
	marker := hex.EncodeToString(digest[:6])
	workflowType := "VCF_INSTALLER_WORKFLOW"
	timestamps := []string{
		"2026-07-14T15:00:04Z",
		"2026-07-14T15:00:01Z",
		"2026-07-14T15:00:03Z",
		"2026-07-14T15:00:01Z",
		"2026-07-14T15:00:02Z",
	}
	tasks := make([]Task, len(timestamps))
	for index, timestamp := range timestamps {
		tasks[index] = Task{
			ID:                fmt.Sprintf("task-%s-%d", marker, index),
			Name:              fmt.Sprintf("installer-work-%s-%d", marker, index),
			Type:              &workflowType,
			Status:            []string{"SUCCESSFUL", "IN_PROGRESS"}[index%2],
			CreationTimestamp: timestamp,
		}
	}
	tasks[2].Type = nil

	s := &Server{
		routes:    routes,
		mode:      mode,
		pageSize:  2,
		tasks:     tasks,
		oldToken:  "old-" + marker,
		newToken:  "new-" + marker,
		refreshID: "refresh-" + marker,
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("start loopback mock: %v", err)
	}
	s.httpServer = httptest.NewUnstartedServer(http.HandlerFunc(s.serveHTTP))
	s.httpServer.Listener = listener
	s.httpServer.Start()
	parsed, err := url.Parse(s.httpServer.URL)
	if err != nil {
		s.httpServer.Close()
		t.Fatalf("parse mock URL: %v", err)
	}
	host, _, err := net.SplitHostPort(parsed.Host)
	if err != nil || net.ParseIP(host) == nil || !net.ParseIP(host).IsLoopback() {
		s.httpServer.Close()
		t.Fatalf("mock did not bind to loopback: %q", parsed.Host)
	}
	t.Cleanup(s.Close)
	return s
}

// URL returns the loopback service root.
func (s *Server) URL() string { return s.httpServer.URL }

// PageSize returns the mock's requested page size.
func (s *Server) PageSize() int { return s.pageSize }

// OldToken returns the access token accepted before expiry.
func (s *Server) OldToken() string { return s.oldToken }

// NewToken returns the replacement access token.
func (s *Server) NewToken() string { return s.newToken }

// RefreshTokenID returns the JSON string required by refreshAccessToken.
func (s *Server) RefreshTokenID() string { return s.refreshID }

// Tasks returns a deep copy of the scenario's unsorted task records.
func (s *Server) Tasks() []Task {
	result := make([]Task, len(s.tasks))
	for index, task := range s.tasks {
		result[index] = task
		if task.Type != nil {
			value := *task.Type
			result[index].Type = &value
		}
	}
	return result
}

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

// Close stops the loopback server.
func (s *Server) Close() { s.httpServer.Close() }

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)

	s.mu.Lock()
	operationID := s.match(r.Method, r.URL.EscapedPath())
	status, response := s.dispatchLocked(operationID, r, body)
	s.requests = append(s.requests, Request{
		OperationID:      operationID,
		Method:           r.Method,
		RawTarget:        r.RequestURI,
		Header:           r.Header.Clone(),
		Body:             append([]byte(nil), body...),
		ContentLength:    r.ContentLength,
		TransferEncoding: append([]string(nil), r.TransferEncoding...),
		ResponseStatus:   status,
	})
	s.mu.Unlock()

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(response)
}

func (s *Server) match(method, escapedPath string) string {
	for _, operationID := range []string{"getTasks", "refreshAccessToken"} {
		route := s.routes[operationID]
		if method == route.Method && escapedPath == route.Path {
			return operationID
		}
	}
	return ""
}

func (s *Server) dispatchLocked(operationID string, r *http.Request, body []byte) (int, any) {
	switch operationID {
	case "getTasks":
		return s.getTasksLocked(r, body)
	case "refreshAccessToken":
		return s.refreshLocked(r, body)
	default:
		return http.StatusNotFound, errorBody("NOT_IN_CONTRACT", "operation is outside the focused contract")
	}
}

func (s *Server) getTasksLocked(r *http.Request, body []byte) (int, any) {
	pageNumber := len(s.successfulPages)
	wantQuery := "pageSize=" + strconv.Itoa(s.pageSize)
	if pageNumber > 0 {
		wantQuery = "pageNumber=" + strconv.Itoa(pageNumber) + "&pageSize=" + strconv.Itoa(s.pageSize)
	}
	if len(body) != 0 || r.URL.RawQuery != wantQuery {
		return http.StatusBadRequest, errorBody("WIRE_SHAPE", "unexpected task query or body")
	}
	wantAuthorization := "Bearer " + s.oldToken
	if s.refreshed {
		wantAuthorization = "Bearer " + s.newToken
	}
	if !hasSingleHeader(r.Header, "Authorization", wantAuthorization) {
		return http.StatusForbidden, errorBody("AUTHORIZATION", "unexpected access token")
	}

	if pageNumber == 1 && !s.challengeSent {
		if s.mode == FailWith500 {
			return http.StatusInternalServerError, errorBody("SERVER_ERROR", "injected failure")
		}
		s.challengeSent = true
		return http.StatusUnauthorized, errorBody("ACCESS_TOKEN_EXPIRED", "access token expired")
	}
	if pageNumber == 1 && s.refreshed && s.mode == SecondUnauthorized {
		return http.StatusUnauthorized, errorBody("ACCESS_TOKEN_EXPIRED", "replacement token rejected")
	}

	totalPages := (len(s.tasks) + s.pageSize - 1) / s.pageSize
	if pageNumber >= totalPages {
		return http.StatusBadRequest, errorBody("PAGE_RANGE", "page is outside the scenario")
	}
	start := pageNumber * s.pageSize
	end := start + s.pageSize
	if end > len(s.tasks) {
		end = len(s.tasks)
	}
	elements := append([]Task(nil), s.tasks[start:end]...)
	if pageNumber%2 == 0 {
		for left, right := 0, len(elements)-1; left < right; left, right = left+1, right-1 {
			elements[left], elements[right] = elements[right], elements[left]
		}
	}
	s.successfulPages = append(s.successfulPages, pageNumber)
	return http.StatusOK, map[string]any{
		"elements": elements,
		"pageMetadata": map[string]int{
			"pageNumber":    pageNumber,
			"pageSize":      s.pageSize,
			"totalElements": len(s.tasks),
			"totalPages":    totalPages,
		},
	}
}

func (s *Server) refreshLocked(r *http.Request, body []byte) (int, any) {
	if r.URL.RawQuery != "" || !s.challengeSent || s.refreshed {
		return http.StatusConflict, errorBody("REFRESH_SEQUENCE", "refresh is out of sequence")
	}
	if !hasSingleHeader(r.Header, "Authorization", "Bearer "+s.oldToken) {
		return http.StatusForbidden, errorBody("AUTHORIZATION", "refresh used the wrong token")
	}
	if !hasSingleHeader(r.Header, "Content-Type", "application/json") {
		return http.StatusUnsupportedMediaType, errorBody("MEDIA_TYPE", "refresh must be JSON")
	}
	wantBody, _ := json.Marshal(s.refreshID)
	if string(body) != string(wantBody) {
		return http.StatusBadRequest, errorBody("WIRE_SHAPE", "refresh body must be the refresh-token ID JSON string")
	}
	if s.mode == RefreshUnauthorized {
		return http.StatusUnauthorized, errorBody("ACCESS_TOKEN_EXPIRED", "refresh rejected")
	}
	s.refreshed = true
	return http.StatusOK, s.newToken
}

func hasSingleHeader(header http.Header, name, want string) bool {
	values := header.Values(name)
	return len(values) == 1 && values[0] == want
}

func errorBody(code, message string) map[string]any {
	return map[string]any{"errorCode": code, "message": message, "arguments": []any{}}
}

// String makes unexpected request records compact in test failures.
func (r Request) String() string {
	return fmt.Sprintf("%s %s (%s => %d)", r.Method, r.RawTarget, r.OperationID, r.ResponseStatus)
}

// ForbiddenQueryMembers returns the unset optional names pinned by the exercise.
func ForbiddenQueryMembers() []string {
	return strings.Fields("limit taskStatus taskType resourceId resourceType completedAfter orderDirection orderBy taskName doLiveRefresh")
}
