// Package mocknsx provides the contract-pinned loopback NSX server used by the
// acceptance verifier. It is test infrastructure, not an NSX implementation.
package mocknsx

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"regexp"
	"strings"
	"sync"
)

const pinnedCommit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"

var pinnedOperationIDs = map[string]bool{
	"PatchInfraSegment":    true,
	"ListRealizedEntities": true,
	"ListAlarms":           true,
}

type contractFile struct {
	Source struct {
		CommitSHA string `json:"commit_sha"`
		SpecPath  string `json:"spec_path"`
	} `json:"source"`
	BasePath   string              `json:"base_path"`
	Operations []contractOperation `json:"operations"`
}

type contractOperation struct {
	OperationID string `json:"operation_id"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

type route struct {
	contractOperation
	pattern *regexp.Regexp
}

// Request is one immutable entry in the server's request log.
type Request struct {
	OperationID string
	Method      string
	Path        string
	EscapedPath string
	RawQuery    string
	Header      http.Header
	Body        []byte
}

// Response is queued explicitly by a test for one contract operation.
type Response struct {
	Status int
	Header http.Header
	Body   string
}

// Server is a loopback HTTP server. Mutable response queues and the request log
// are synchronized so the race detector can exercise concurrent callers.
type Server struct {
	httpServer *httptest.Server
	httpClient *http.Client
	origin     string
	routes     []route
	mu         sync.Mutex
	responses  map[string][]Response
	requests   []Request
}

// New loads the protected contract and starts a loopback-only server.
func New(contractPath string) (*Server, error) {
	raw, err := os.ReadFile(contractPath)
	if err != nil {
		return nil, fmt.Errorf("read contract: %w", err)
	}
	var contract contractFile
	if err := json.Unmarshal(raw, &contract); err != nil {
		return nil, fmt.Errorf("decode contract: %w", err)
	}
	if contract.Source.CommitSHA != pinnedCommit {
		return nil, fmt.Errorf("contract commit is %q, want %q", contract.Source.CommitSHA, pinnedCommit)
	}
	if contract.Source.SpecPath != "specifications/nsx/openapi-2.0/nsx_policy_api.yaml" {
		return nil, fmt.Errorf("unexpected contract spec path %q", contract.Source.SpecPath)
	}
	if contract.BasePath != "/policy/api/v1" {
		return nil, fmt.Errorf("unexpected contract base path %q", contract.BasePath)
	}
	if len(contract.Operations) != len(pinnedOperationIDs) {
		return nil, fmt.Errorf("contract has %d operations, want %d", len(contract.Operations), len(pinnedOperationIDs))
	}

	server := &Server{responses: make(map[string][]Response)}
	seen := make(map[string]bool)
	for _, operation := range contract.Operations {
		if !pinnedOperationIDs[operation.OperationID] || seen[operation.OperationID] {
			return nil, fmt.Errorf("unexpected or duplicate operationId %q", operation.OperationID)
		}
		seen[operation.OperationID] = true
		pattern, err := compileTemplate(contract.BasePath + operation.Path)
		if err != nil {
			return nil, fmt.Errorf("%s path: %w", operation.OperationID, err)
		}
		server.routes = append(server.routes, route{contractOperation: operation, pattern: pattern})
	}
	for operationID := range pinnedOperationIDs {
		if !seen[operationID] {
			return nil, fmt.Errorf("contract is missing operationId %q", operationID)
		}
	}

	listener, listenErr := net.Listen("tcp4", "127.0.0.1:0")
	if listenErr == nil {
		httpServer := httptest.NewUnstartedServer(http.HandlerFunc(server.serveHTTP))
		httpServer.Listener = listener
		httpServer.Start()
		server.httpServer = httpServer
		server.httpClient = httpServer.Client()
		server.origin = httpServer.URL
	} else {
		// Some hermetic authoring sandboxes prohibit the socket syscall itself.
		// Keep the same loopback-origin HTTP wire contract there and dispatch it
		// directly to the handler. Normal verifier environments use the listener.
		server.origin = "http://127.0.0.1"
		server.httpClient = &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
			if err := request.Context().Err(); err != nil {
				return nil, err
			}
			recorder := httptest.NewRecorder()
			server.serveHTTP(recorder, request)
			return recorder.Result(), nil
		})}
	}
	return server, nil
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (function roundTripFunc) RoundTrip(request *http.Request) (*http.Response, error) {
	return function(request)
}

func compileTemplate(template string) (*regexp.Regexp, error) {
	var expression strings.Builder
	expression.WriteString("^")
	for len(template) > 0 {
		open := strings.IndexByte(template, '{')
		if open < 0 {
			expression.WriteString(regexp.QuoteMeta(template))
			break
		}
		expression.WriteString(regexp.QuoteMeta(template[:open]))
		closeOffset := strings.IndexByte(template[open:], '}')
		if closeOffset < 0 {
			return nil, fmt.Errorf("unclosed path parameter")
		}
		closeIndex := open + closeOffset
		if closeIndex == open+1 {
			return nil, fmt.Errorf("empty path parameter")
		}
		expression.WriteString("[^/]+")
		template = template[closeIndex+1:]
	}
	expression.WriteString("$")
	return regexp.Compile(expression.String())
}

// URL returns the loopback origin, without the NSX base path.
func (s *Server) URL() string {
	return s.origin
}

// Client returns an HTTP client wired only to this mock.
func (s *Server) Client() *http.Client {
	return s.httpClient
}

// Close stops the loopback server.
func (s *Server) Close() {
	if s.httpServer != nil {
		s.httpServer.Close()
	}
}

// Queue appends responses for a named operation. Unknown operationIds are
// rejected, so the mock cannot be extended beyond the protected contract.
func (s *Server) Queue(operationID string, responses ...Response) error {
	if !pinnedOperationIDs[operationID] {
		return fmt.Errorf("operationId %q is not in the pinned contract", operationID)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.responses[operationID] = append(s.responses[operationID], responses...)
	return nil
}

// Requests returns a deep copy of the request log.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	result := make([]Request, len(s.requests))
	for index, request := range s.requests {
		result[index] = request
		result[index].Header = request.Header.Clone()
		result[index].Body = append([]byte(nil), request.Body...)
	}
	return result
}

func (s *Server) serveHTTP(writer http.ResponseWriter, request *http.Request) {
	var body []byte
	if request.Body != nil {
		body, _ = io.ReadAll(io.LimitReader(request.Body, 1<<20))
	}
	operationID := ""
	for _, candidate := range s.routes {
		if request.Method == candidate.Method && candidate.pattern.MatchString(request.URL.EscapedPath()) {
			operationID = candidate.OperationID
			break
		}
	}

	entry := Request{
		OperationID: operationID,
		Method:      request.Method,
		Path:        request.URL.Path,
		EscapedPath: request.URL.EscapedPath(),
		RawQuery:    request.URL.RawQuery,
		Header:      request.Header.Clone(),
		Body:        append([]byte(nil), body...),
	}

	s.mu.Lock()
	s.requests = append(s.requests, entry)
	var response Response
	configured := false
	if operationID != "" && len(s.responses[operationID]) > 0 {
		response = s.responses[operationID][0]
		s.responses[operationID] = s.responses[operationID][1:]
		configured = true
	}
	s.mu.Unlock()

	if operationID == "" {
		http.Error(writer, "operation is not in the pinned contract", http.StatusNotFound)
		return
	}
	if !configured {
		http.Error(writer, "no response queued for operation", http.StatusInternalServerError)
		return
	}
	for name, values := range response.Header {
		for _, value := range values {
			writer.Header().Add(name, value)
		}
	}
	status := response.Status
	if status == 0 {
		status = http.StatusOK
	}
	writer.WriteHeader(status)
	_, _ = io.WriteString(writer, response.Body)
}
