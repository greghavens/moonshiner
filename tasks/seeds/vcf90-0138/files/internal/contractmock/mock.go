package contractmock

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
)

const (
	nextCursor  = "page+2/=="
	thirdCursor = "page+3/=="
)

const contractSHA256 = "490cd196d6444040d5e9a9d3c78504579f14bd5d98973f7fa8442fcc5a7f6e7a"

type contract struct {
	ServerBasePath string      `json:"serverBasePath"`
	Operations     []operation `json:"operations"`
}

type operation struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

// Request is the wire-relevant portion of a request received by Server.
type Request struct {
	Method        string
	RequestURI    string
	Authorization string
	Body          []byte
}

// ResponseOverride makes the mock return one fixed response after validating
// the request route. It is used by the verifier to exercise HTTP and decoding
// failures without contacting any service other than the loopback mock.
type ResponseOverride struct {
	StatusCode int
	Body       string
}

// Server is a loopback-only server for the operations named by contract.json.
// Its request log is synchronized so it is safe to inspect under go test -race.
type Server struct {
	httpServer *httptest.Server
	client     *http.Client
	basePath   string
	operation  operation
	override   *ResponseOverride
	pageCount  int

	mu       sync.Mutex
	requests []Request
}

// New loads the supplied contract and starts a loopback mock pinned to its sole
// listTroubleshootingIncidents operation.
func New(contractPath string) (*Server, error) {
	return newServer(contractPath, 2, nil)
}

// NewWithResponse is New with an optional fixed response for every valid
// operation request.
func NewWithResponse(contractPath string, override *ResponseOverride) (*Server, error) {
	return newServer(contractPath, 2, override)
}

// NewWithPages is New with a cursor chain of the requested length. The
// three-page fixture includes an empty middle page with a non-empty cursor.
func NewWithPages(contractPath string, pageCount int) (*Server, error) {
	if pageCount != 2 && pageCount != 3 {
		return nil, fmt.Errorf("unsupported page count %d", pageCount)
	}
	return newServer(contractPath, pageCount, nil)
}

func newServer(contractPath string, pageCount int, override *ResponseOverride) (*Server, error) {
	raw, err := os.ReadFile(contractPath)
	if err != nil {
		return nil, fmt.Errorf("read contract: %w", err)
	}
	if sum := fmt.Sprintf("%x", sha256.Sum256(raw)); sum != contractSHA256 {
		return nil, fmt.Errorf("contract digest mismatch: got %s", sum)
	}

	var c contract
	if err := json.Unmarshal(raw, &c); err != nil {
		return nil, fmt.Errorf("decode contract: %w", err)
	}
	if c.ServerBasePath != "/api/ni" || len(c.Operations) != 1 {
		return nil, fmt.Errorf("unexpected contract routing")
	}
	op := c.Operations[0]
	if op.OperationID != "listTroubleshootingIncidents" || op.Method != http.MethodGet || op.Path != "/gnt/troubleshoot/incidents" {
		return nil, fmt.Errorf("unexpected contract operation")
	}

	s := &Server{basePath: c.ServerBasePath, operation: op, pageCount: pageCount}
	if override != nil {
		if override.StatusCode < 100 || override.StatusCode > 999 {
			return nil, fmt.Errorf("invalid override status %d", override.StatusCode)
		}
		copy := *override
		s.override = &copy
	}
	s.httpServer = httptest.NewServer(http.HandlerFunc(s.serveHTTP))
	s.client = s.httpServer.Client()
	return s, nil
}

// URL returns the base URL a client should use, including the contract's server path.
func (s *Server) URL() string {
	return s.httpServer.URL + s.basePath
}

// Client returns an HTTP client configured for this loopback server.
func (s *Server) Client() *http.Client {
	return s.client
}

// Close releases the loopback listener.
func (s *Server) Close() {
	s.httpServer.Close()
}

// Requests returns a deep copy of the requests received so far.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()

	requests := make([]Request, len(s.requests))
	for i, request := range s.requests {
		requests[i] = request
		requests[i].Body = append([]byte(nil), request.Body...)
	}
	return requests
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	s.mu.Lock()
	s.requests = append(s.requests, Request{
		Method:        r.Method,
		RequestURI:    r.RequestURI,
		Authorization: r.Header.Get("Authorization"),
		Body:          append([]byte(nil), body...),
	})
	s.mu.Unlock()

	contractPath := s.basePath + s.operation.Path
	if r.Method != s.operation.Method || r.URL.Path != contractPath {
		http.NotFound(w, r)
		return
	}

	w.Header().Set("Content-Type", "application/json")
	if s.override != nil {
		w.WriteHeader(s.override.StatusCode)
		_, _ = io.WriteString(w, s.override.Body)
		return
	}
	switch r.URL.Query().Get("cursor") {
	case "":
		_, _ = io.WriteString(w, `{"results":[{"entity_id":"entity-030","start_entity_id":"vm-3","name":"Third","status":"COMPLETED"},{"entity_id":"entity-010","start_entity_id":"vm-1","name":"First","status":"RUNNING"}],"total_count":4,"cursor":"`+nextCursor+`"}`)
	case nextCursor:
		if s.pageCount == 3 {
			_, _ = io.WriteString(w, `{"results":[],"total_count":4,"cursor":"`+thirdCursor+`"}`)
			return
		}
		_, _ = io.WriteString(w, `{"results":[{"entity_id":"entity-020","start_entity_id":"vm-2","name":"Second","status":"FAILED"},{"entity_id":"entity-015","start_entity_id":"vm-15","name":"Between","status":"COMPLETED"}],"total_count":4}`)
	case thirdCursor:
		if s.pageCount != 3 {
			http.Error(w, strings.TrimSpace("unexpected cursor"), http.StatusBadRequest)
			return
		}
		_, _ = io.WriteString(w, `{"results":[{"entity_id":"entity-020","start_entity_id":"vm-2","name":"Second","status":"FAILED"},{"entity_id":"entity-015","start_entity_id":"vm-15","name":"Between","status":"COMPLETED"}],"total_count":4}`)
	default:
		http.Error(w, strings.TrimSpace("unexpected cursor"), http.StatusBadRequest)
	}
}
