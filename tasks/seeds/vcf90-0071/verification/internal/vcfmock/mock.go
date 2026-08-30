// Package vcfmock provides a contract-pinned loopback server for the two VCF
// Operations content-import operations used by this repository.
package vcfmock

import (
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"sync"
)

const ImportPath = "/suite-api/api/content/operations/import"

// Request is an immutable request-log entry captured before a response is sent.
type Request struct {
	Method     string
	RequestURI string
	Path       string
	RawQuery   string
	Header     http.Header
	Body       []byte
}

// Server is a loopback VCF Operations server with a race-safe request log.
type Server struct {
	url    string
	client *http.Client
	close  func()

	mu         sync.Mutex
	requests   []Request
	states     []string
	poll       int
	postStatus int
	pollStatus int
}

// New starts a loopback server. Each GET consumes one state; after the supplied
// sequence is exhausted, the final state is repeated.
func New(states ...string) *Server {
	if len(states) == 0 {
		states = []string{"FINISHED"}
	}
	s := &Server{
		states:     append([]string(nil), states...),
		postStatus: http.StatusAccepted,
		pollStatus: http.StatusOK,
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err == nil {
		server := httptest.NewUnstartedServer(http.HandlerFunc(s.serveHTTP))
		server.Listener = listener
		server.Start()
		s.url = server.URL
		s.client = server.Client()
		s.close = server.Close
	} else {
		// Some hermetic runners deny socket creation entirely. Keep the fixture
		// useful there without changing its HTTP contract; ordinary runs always
		// take the real 127.0.0.1 listener path above.
		s.url = "http://127.0.0.1"
		s.client = &http.Client{Transport: roundTripperFunc(func(request *http.Request) (*http.Response, error) {
			request = request.Clone(request.Context())
			request.RequestURI = request.URL.RequestURI()
			recorder := httptest.NewRecorder()
			s.serveHTTP(recorder, request)
			return recorder.Result(), nil
		})}
		s.close = func() {}
	}
	return s
}

type roundTripperFunc func(*http.Request) (*http.Response, error)

func (function roundTripperFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	var body []byte
	if r.Body != nil {
		body, _ = io.ReadAll(r.Body)
	}
	s.mu.Lock()
	s.requests = append(s.requests, Request{
		Method:     r.Method,
		RequestURI: r.RequestURI,
		Path:       r.URL.Path,
		RawQuery:   r.URL.RawQuery,
		Header:     r.Header.Clone(),
		Body:       append([]byte(nil), body...),
	})

	if r.URL.Path != ImportPath || (r.Method != http.MethodPost && r.Method != http.MethodGet) {
		s.mu.Unlock()
		http.NotFound(w, r)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if r.Method == http.MethodPost {
		status := s.postStatus
		s.mu.Unlock()
		w.WriteHeader(status)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"fileName": "content.zip",
			"force":    false,
			"id":       "bb1f4c64-b1e9-4a3f-a051-790d926342d2",
			"links": []map[string]string{{
				"href": "/suite-api/api/content/operations/import",
				"rel":  "RELATED",
				"name": "ImportStatusCheckURL",
			}},
		})
		return
	}

	index := s.poll
	if index >= len(s.states) {
		index = len(s.states) - 1
	}
	state := s.states[index]
	status := s.pollStatus
	s.poll++
	s.mu.Unlock()

	response := map[string]any{
		"id":              "bb1f4c64-b1e9-4a3f-a051-790d926342d2",
		"type":            "IMPORT",
		"state":           state,
		"lastUpdatedTime": int64(1625238320326),
		"errorCode":       "NONE",
		"errorMessages":   []string{},
	}
	if state == "FAILED" || state == "UNKNOWN" {
		response["errorCode"] = "OPERATION_FAILED"
		response["errorMessages"] = []string{"fixture import failed"}
	}
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(response)
}

// URL returns the loopback server's origin URL.
func (s *Server) URL() string { return s.url }

// Client returns the HTTP client configured for this loopback server.
func (s *Server) Client() *http.Client { return s.client }

// Requests returns a deep copy of the request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	result := make([]Request, len(s.requests))
	for i, request := range s.requests {
		result[i] = request
		result[i].Header = request.Header.Clone()
		result[i].Body = append([]byte(nil), request.Body...)
	}
	return result
}

// SetPostStatus changes the status returned by importContent while preserving
// its otherwise-valid response body.
func (s *Server) SetPostStatus(status int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.postStatus = status
}

// SetPollStatus changes the status returned by getLastImportOperation while
// preserving its otherwise-valid response body.
func (s *Server) SetPollStatus(status int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pollStatus = status
}

// Close stops the loopback server.
func (s *Server) Close() { s.close() }
