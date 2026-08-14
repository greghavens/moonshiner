// Package mockdp provides the loopback vSAN Data Protection fixture used by
// the acceptance verifier. It implements only the operations pinned in
// docs/contract.json.
package mockdp

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"regexp"
	"sync"
)

const (
	createSnapshotOperationID = "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task"
	getTaskOperationID        = "Snapservice.Tasks_get"
)

var (
	createSnapshotPath = regexp.MustCompile(`^/api/snapservice/clusters/[^/]+/protection-groups/[^/]+/snapshots$`)
	getTaskPath        = regexp.MustCompile(`^/api/snapservice/tasks/[^/]+$`)
)

// OperationIDs returns a copy of the exact operations served by the mock.
func OperationIDs() []string {
	return []string{createSnapshotOperationID, getTaskOperationID}
}

// Request is the verifier-readable wire log for one request.
type Request struct {
	Method      string
	RequestURI  string
	SessionID   string
	ContentType string
	Accept      string
	Body        string
}

// Config defines one deterministic snapshot run. The initial token is valid
// for snapshot creation and expires before the first task read.
type Config struct {
	InitialToken   string
	RefreshedToken string
	TaskID         string
	TaskStatuses   []string
}

// Server is a loopback-only HTTP server with a race-safe request log.
type Server struct {
	config Config
	http   *httptest.Server

	mu               sync.Mutex
	requests         []Request
	createdSnapshots int
	statusIndex      int
}

// New starts a loopback mock pinned to the two contract operations.
func New(config Config) *Server {
	config.TaskStatuses = append([]string(nil), config.TaskStatuses...)
	s := &Server{config: config}
	s.http = httptest.NewServer(http.HandlerFunc(s.serveHTTP))
	return s
}

// URL is the server URL including the OpenAPI document's /api base path.
func (s *Server) URL() string {
	return s.http.URL + "/api"
}

// Client returns the loopback server's HTTP client.
func (s *Server) Client() *http.Client {
	return s.http.Client()
}

// Close stops the loopback server.
func (s *Server) Close() {
	s.http.Close()
}

// Requests returns a snapshot of the request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]Request(nil), s.requests...)
}

// CreatedSnapshots reports how many authorized create operations were
// accepted, allowing the verifier to detect duplicated work after refresh.
func (s *Server) CreatedSnapshots() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.createdSnapshots
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "request body read failed", http.StatusInternalServerError)
		return
	}
	s.record(Request{
		Method:      r.Method,
		RequestURI:  r.RequestURI,
		SessionID:   r.Header.Get("vmware-api-session-id"),
		ContentType: r.Header.Get("Content-Type"),
		Accept:      r.Header.Get("Accept"),
		Body:        string(body),
	})

	escapedPath := r.URL.EscapedPath()
	switch {
	case r.Method == http.MethodPost &&
		createSnapshotPath.MatchString(escapedPath) &&
		r.URL.RawQuery == "vmw-task=true":
		s.serveCreateSnapshot(w, r)
	case r.Method == http.MethodGet &&
		getTaskPath.MatchString(escapedPath) &&
		r.URL.RawQuery == "":
		s.serveGetTask(w, r)
	default:
		writeJSON(w, http.StatusNotFound, map[string]any{
			"error_type": "NOT_FOUND",
			"messages":   []any{},
		})
	}
}

func (s *Server) serveCreateSnapshot(w http.ResponseWriter, r *http.Request) {
	token := r.Header.Get("vmware-api-session-id")
	if token != s.config.InitialToken && token != s.config.RefreshedToken {
		writeUnauthorized(w)
		return
	}

	s.mu.Lock()
	s.createdSnapshots++
	s.mu.Unlock()
	writeJSON(w, http.StatusAccepted, s.config.TaskID)
}

func (s *Server) serveGetTask(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("vmware-api-session-id") != s.config.RefreshedToken {
		writeUnauthorized(w)
		return
	}

	status := s.nextStatus()
	writeJSON(w, http.StatusOK, map[string]any{
		"cancelable": false,
		"description": map[string]any{
			"id":              "com.vmware.snapservice.task",
			"default_message": "snapshot task " + status,
			"args":            []string{},
		},
		"operation": createSnapshotOperationID,
		"service":   "com.vmware.snapservice",
		"status":    status,
	})
}

func (s *Server) nextStatus() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.config.TaskStatuses) == 0 {
		return "SUCCEEDED"
	}
	index := s.statusIndex
	if index >= len(s.config.TaskStatuses) {
		index = len(s.config.TaskStatuses) - 1
	} else {
		s.statusIndex++
	}
	return s.config.TaskStatuses[index]
}

func (s *Server) record(request Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, request)
}

func writeUnauthorized(w http.ResponseWriter) {
	writeJSON(w, http.StatusUnauthorized, map[string]any{
		"error_type": "UNAUTHENTICATED",
		"messages":   []any{},
	})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	body, err := json.Marshal(value)
	if err != nil {
		panic(err)
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}
