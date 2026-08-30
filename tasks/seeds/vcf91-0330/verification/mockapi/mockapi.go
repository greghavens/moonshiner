// Package mockapi serves a loopback stand-in for the VCF Automation deployment
// API.
//
// The server is pinned to docs/contract.json: it routes only the operations the
// contract names, and it records every request it receives so a test can assert
// the exact wire shape the client produced.
package mockapi

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
)

// Event is one event emitted by a deployment request.
type Event struct {
	ID           string
	Name         string
	Details      string
	Timestamp    string
	ResourceName string
	ResourceType string

	// HasLogs reports whether the event has logs to retrieve. An event with
	// HasLogs false has no log resource: asking for its logs is a 404.
	HasLogs bool

	// UserEvent marks an event raised by a user rather than the engine.
	UserEvent bool

	// Logs are the log line messages, in row order. Row numbers start at 1.
	Logs []string
}

// Request is one deployment request.
type Request struct {
	ID        string
	Name      string
	Status    string
	Details   string
	CreatedAt string
	UpdatedAt string

	Events []Event
}

// Deployment is the seeded state of one deployment.
type Deployment struct {
	// Requests are served in the order they are seeded here. The server does
	// not sort them.
	Requests []Request
}

// Options configures a Server.
type Options struct {
	// ContractPath is the path to docs/contract.json. Required.
	ContractPath string

	// Token is the bearer token the server requires. Required.
	Token string

	// Deployments is the seeded state, keyed by deployment id.
	Deployments map[string]Deployment

	// LogPageSize is the number of log rows served per response. When zero or
	// negative every remaining row is served in one response.
	LogPageSize int

	// Each of these, when non-zero, makes the matching operation respond with
	// that status code instead of its normal response.
	RequestsStatus int // getDeploymentRequests
	RequestStatus  int // getRequest
	EventsStatus   int // getRequestEvents
	LogsStatus     int // getEventLogs
}

// Recorded is one request the server received, in the order it arrived.
type Recorded struct {
	Method   string
	Path     string
	RawQuery string
	Header   http.Header
	Body     []byte
}

// Server is a running loopback API.
type Server struct {
	url  string
	http *http.Server
	opts Options

	mu  sync.Mutex
	log []Recorded
}

// contract is the subset of docs/contract.json the mock pins itself to.
type contract struct {
	Operations []struct {
		ID     string `json:"id"`
		Method string `json:"method"`
		Path   string `json:"path"`
	} `json:"operations"`
}

// The operations this mock knows how to serve, and the paths it expects for
// them. A contract naming anything else is refused.
var served = map[string]string{
	"getDeploymentRequests": "/deployment/api/deployments/{deploymentId}/requests",
	"getRequest":            "/deployment/api/requests/{requestId}",
	"getRequestEvents":      "/deployment/api/requests/{requestId}/events",
	"getEventLogs":          "/deployment/api/requests/{requestId}/events/{eventId}/logs",
}

// Start reads the contract, registers a route for each operation it names and
// starts listening on loopback.
func Start(opts Options) (*Server, error) {
	if opts.ContractPath == "" {
		return nil, fmt.Errorf("mockapi: ContractPath is required")
	}
	if opts.Token == "" {
		return nil, fmt.Errorf("mockapi: Token is required")
	}

	raw, err := os.ReadFile(opts.ContractPath)
	if err != nil {
		return nil, fmt.Errorf("mockapi: reading contract: %w", err)
	}
	var c contract
	if err := json.Unmarshal(raw, &c); err != nil {
		return nil, fmt.Errorf("mockapi: parsing contract %s: %w", opts.ContractPath, err)
	}
	if len(c.Operations) == 0 {
		return nil, fmt.Errorf("mockapi: contract %s names no operations", opts.ContractPath)
	}

	s := &Server{opts: opts}
	mux := http.NewServeMux()

	for _, op := range c.Operations {
		want, ok := served[op.ID]
		if !ok {
			return nil, fmt.Errorf("mockapi: contract names operation %q, which this mock does not serve", op.ID)
		}
		if op.Path != want {
			return nil, fmt.Errorf("mockapi: operation %q has path %q, want %q", op.ID, op.Path, want)
		}
		method := op.Method
		if method == "" {
			method = http.MethodGet
		}

		var h http.HandlerFunc
		switch op.ID {
		case "getDeploymentRequests":
			h = s.handleDeploymentRequests
		case "getRequest":
			h = s.handleRequest
		case "getRequestEvents":
			h = s.handleRequestEvents
		case "getEventLogs":
			h = s.handleEventLogs
		}
		// Go's ServeMux takes the contract's template verbatim: {deploymentId}
		// and friends are wildcards, and a routed path reached with another
		// method answers 405 on its own.
		mux.HandleFunc(method+" "+op.Path, s.authed(h))
	}

	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("mockapi: listen on loopback: %w", err)
	}
	s.http = &http.Server{Handler: s.record(mux)}
	s.url = "http://" + listener.Addr().String()
	go func() { _ = s.http.Serve(listener) }()
	return s, nil
}

// URL is the scheme://host:port root the server is listening on.
func (s *Server) URL() string { return s.url }

// Close shuts the server down.
func (s *Server) Close() {
	if s.http != nil {
		s.http.Close()
	}
}

// Requests returns a copy of the request log, oldest first.
func (s *Server) Requests() []Recorded {
	s.mu.Lock()
	defer s.mu.Unlock()

	out := make([]Recorded, len(s.log))
	for i, rec := range s.log {
		cp := rec
		cp.Header = rec.Header.Clone()
		if rec.Body != nil {
			cp.Body = append([]byte(nil), rec.Body...)
		}
		out[i] = cp
	}
	return out
}

// record logs every request, routed or not, before handing it on.
func (s *Server) record(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
		r.Body.Close()

		s.mu.Lock()
		s.log = append(s.log, Recorded{
			Method:   r.Method,
			Path:     r.URL.Path,
			RawQuery: r.URL.RawQuery,
			Header:   r.Header.Clone(),
			Body:     body,
		})
		s.mu.Unlock()

		r.Body = io.NopCloser(strings.NewReader(string(body)))
		next.ServeHTTP(w, r)
	})
}

// authed rejects a missing or wrong bearer token. The request is recorded
// either way, because record wraps the whole mux.
func (s *Server) authed(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+s.opts.Token {
			writeJSON(w, http.StatusUnauthorized, map[string]any{
				"message": "unauthorized",
			})
			return
		}
		next(w, r)
	}
}

// ---------------------------------------------------------------------------
// handlers
// ---------------------------------------------------------------------------

func (s *Server) handleDeploymentRequests(w http.ResponseWriter, r *http.Request) {
	if s.opts.RequestsStatus != 0 {
		writeStatus(w, s.opts.RequestsStatus)
		return
	}
	dep, ok := s.opts.Deployments[r.PathValue("deploymentId")]
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]any{"message": "deployment not found"})
		return
	}

	// Served in seeded order; the mock records sort and size but does not apply
	// them, so a test can see exactly what the client asked for.
	content := make([]map[string]any, 0, len(dep.Requests))
	for _, req := range dep.Requests {
		content = append(content, requestJSON(req))
	}
	writeJSON(w, http.StatusOK, pageJSON(content))
}

func (s *Server) handleRequest(w http.ResponseWriter, r *http.Request) {
	if s.opts.RequestStatus != 0 {
		writeStatus(w, s.opts.RequestStatus)
		return
	}
	req, ok := s.findRequest(r.PathValue("requestId"))
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]any{"message": "request not found"})
		return
	}
	writeJSON(w, http.StatusOK, requestJSON(req))
}

func (s *Server) handleRequestEvents(w http.ResponseWriter, r *http.Request) {
	if s.opts.EventsStatus != 0 {
		writeStatus(w, s.opts.EventsStatus)
		return
	}
	req, ok := s.findRequest(r.PathValue("requestId"))
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]any{"message": "request not found"})
		return
	}

	content := make([]map[string]any, 0, len(req.Events))
	for _, ev := range req.Events {
		content = append(content, map[string]any{
			"id":           ev.ID,
			"name":         ev.Name,
			"details":      ev.Details,
			"timestamp":    ev.Timestamp,
			"resourceName": ev.ResourceName,
			"resourceType": ev.ResourceType,
			"hasLogs":      ev.HasLogs,
			"userEvent":    ev.UserEvent,
		})
	}
	writeJSON(w, http.StatusOK, pageJSON(content))
}

func (s *Server) handleEventLogs(w http.ResponseWriter, r *http.Request) {
	if s.opts.LogsStatus != 0 {
		writeStatus(w, s.opts.LogsStatus)
		return
	}
	req, ok := s.findRequest(r.PathValue("requestId"))
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]any{"message": "request not found"})
		return
	}
	var ev Event
	var found bool
	for _, e := range req.Events {
		if e.ID == r.PathValue("eventId") {
			ev, found = e, true
			break
		}
	}
	// An event with no logs has no log resource at all.
	if !found || !ev.HasLogs {
		writeJSON(w, http.StatusNotFound, map[string]any{"message": "event logs not found"})
		return
	}

	// sinceRow is a positive row number, inclusive. Absent means from the top.
	from := 1
	if raw := r.URL.Query().Get("sinceRow"); raw != "" {
		n, err := strconv.Atoi(raw)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]any{"message": "sinceRow must be an integer"})
			return
		}
		if n > 1 {
			from = n
		}
	}

	total := len(ev.Logs)
	start := from - 1
	if start > total {
		start = total
	}
	end := total
	if s.opts.LogPageSize > 0 && start+s.opts.LogPageSize < end {
		end = start + s.opts.LogPageSize
	}

	content := make([]map[string]any, 0, end-start)
	for i := start; i < end; i++ {
		rownum := i + 1
		content = append(content, map[string]any{
			// Deterministic: derived from the event and the row, never random.
			"id":        fmt.Sprintf("%s-log-%d", ev.ID, rownum),
			"message":   ev.Logs[i],
			"rownum":    rownum,
			"timestamp": ev.Timestamp,
			"eof":       rownum == total,
		})
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"content":          content,
		"empty":            len(content) == 0,
		"first":            start == 0,
		"last":             end >= total,
		"number":           0,
		"numberOfElements": len(content),
		"size":             len(content),
	})
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

func (s *Server) findRequest(id string) (Request, bool) {
	for _, key := range slicesSortedKeys(s.opts.Deployments) {
		for _, req := range s.opts.Deployments[key].Requests {
			if req.ID == id {
				return req, true
			}
		}
	}
	return Request{}, false
}

// slicesSortedKeys keeps the lookup order stable so the server behaves the same
// way on every run.
func slicesSortedKeys(m map[string]Deployment) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	// Small maps; an insertion sort keeps this dependency-free.
	for i := 1; i < len(keys); i++ {
		for j := i; j > 0 && keys[j] < keys[j-1]; j-- {
			keys[j], keys[j-1] = keys[j-1], keys[j]
		}
	}
	return keys
}

func requestJSON(req Request) map[string]any {
	return map[string]any{
		"id":        req.ID,
		"name":      req.Name,
		"status":    req.Status,
		"details":   req.Details,
		"createdAt": req.CreatedAt,
		"updatedAt": req.UpdatedAt,
	}
}

func pageJSON(content []map[string]any) map[string]any {
	return map[string]any{
		"content":          content,
		"empty":            len(content) == 0,
		"first":            true,
		"last":             true,
		"number":           0,
		"numberOfElements": len(content),
		"size":             len(content),
		"totalElements":    len(content),
		"totalPages":       1,
	}
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

func writeStatus(w http.ResponseWriter, status int) {
	writeJSON(w, status, map[string]any{
		"message": http.StatusText(status),
	})
}
