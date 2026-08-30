// Package contractmock provides a loopback-only server for the two operations
// selected from the protected VCF Installer 9.0 contract.
package contractmock

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
)

const (
	StartBundleDownloadOperationID = "startBundleDownloadByID"
	GetTaskOperationID             = "getTask"
	TaskIDPrefix                   = "task/"
)

// RecordedRequest is an immutable copy of the request wire data used by the
// acceptance tests.
type RecordedRequest struct {
	Method      string
	RequestURI  string
	ContentType string
	Body        []byte
}

// Server is a contract-pinned loopback mock with a concurrency-safe request
// log.
type Server struct {
	*httptest.Server

	mu           sync.Mutex
	requests     []RecordedRequest
	statuses     []string
	polls        int
	pollObserved chan struct{}
	taskID       string
}

// New starts a loopback mock. Each getTask call advances through statuses; the
// final status is repeated if a client polls again.
func New(statuses []string) *Server {
	s := &Server{
		statuses:     append([]string(nil), statuses...),
		pollObserved: make(chan struct{}, 1),
	}
	s.Server = httptest.NewServer(http.HandlerFunc(s.serveHTTP))
	return s
}

// PollObserved is notified whenever the mock receives getTask. It lets tests
// coordinate cancellation without timing sleeps.
func (s *Server) PollObserved() <-chan struct{} { return s.pollObserved }

// Requests returns a deep copy of the request log.
func (s *Server) Requests() []RecordedRequest {
	s.mu.Lock()
	defer s.mu.Unlock()

	out := make([]RecordedRequest, len(s.requests))
	for i, request := range s.requests {
		out[i] = request
		out[i].Body = append([]byte(nil), request.Body...)
	}
	return out
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	s.mu.Lock()
	s.requests = append(s.requests, RecordedRequest{
		Method:      r.Method,
		RequestURI:  r.RequestURI,
		ContentType: r.Header.Get("Content-Type"),
		Body:        append([]byte(nil), body...),
	})
	s.mu.Unlock()

	escapedPath := r.URL.EscapedPath()
	bundleID := strings.TrimPrefix(escapedPath, "/v1/bundles/")
	switch {
	case r.Method == http.MethodPatch && strings.HasPrefix(escapedPath, "/v1/bundles/") && bundleID != "" && !strings.Contains(bundleID, "/"):
		decodedBundleID, err := url.PathUnescape(bundleID)
		if err != nil {
			http.NotFound(w, r)
			return
		}
		s.mu.Lock()
		s.taskID = TaskIDPrefix + decodedBundleID
		taskID := s.taskID
		s.mu.Unlock()
		writeJSON(w, http.StatusAccepted, task(taskID, "PENDING"))
	case r.Method == http.MethodGet && strings.HasPrefix(escapedPath, "/v1/tasks/"):
		select {
		case s.pollObserved <- struct{}{}:
		default:
		}
		s.mu.Lock()
		if escapedPath != "/v1/tasks/"+url.PathEscape(s.taskID) {
			s.mu.Unlock()
			http.NotFound(w, r)
			return
		}
		if len(s.statuses) == 0 {
			s.mu.Unlock()
			http.Error(w, "mock requires at least one status", http.StatusInternalServerError)
			return
		}
		index := s.polls
		if index >= len(s.statuses) {
			index = len(s.statuses) - 1
		}
		status := s.statuses[index]
		taskID := s.taskID
		s.polls++
		s.mu.Unlock()
		writeJSON(w, http.StatusOK, task(taskID, status))
	default:
		http.NotFound(w, r)
	}
}

func task(taskID, status string) map[string]string {
	return map[string]string{
		"id":                taskID,
		"name":              "Bundle download",
		"status":            status,
		"creationTimestamp": "2026-08-13T12:00:00Z",
	}
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
