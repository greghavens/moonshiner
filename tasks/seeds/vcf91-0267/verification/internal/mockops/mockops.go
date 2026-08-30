// Package mockops runs an in-process HTTP stand-in for a VCF Operations appliance.
//
// The server is pinned to docs/contract.json: it builds its routing table from
// the contract's operations and serves nothing else. Any request that does not
// match a contract operation is answered 404 and recorded with an empty
// OperationID, so a test can prove the client stayed inside the contract.
//
// Every request is recorded in order. Requests is safe to call concurrently
// with in-flight requests and returns a copy, so tests pass under -race.
package mockops

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sort"
	"strings"
	"sync"
	"testing"

	"vcfops.local/opsreport/internal/contract"
)

// Request is one recorded inbound HTTP request and the status the mock answered.
type Request struct {
	// Index is the 0-based arrival order.
	Index int
	// OperationID is the contract operation that matched, or "" if none did.
	OperationID string
	Method      string
	// Path is the request path, base path included, with parameters substituted.
	Path string
	// PathParams holds the captured path parameters, keyed by contract name.
	PathParams map[string]string
	// RawQuery is the raw query string, "" when the client sent no query at all.
	RawQuery string
	Query    url.Values
	Header   http.Header
	Body     []byte
	// ResponseStatus is the HTTP status the mock returned.
	ResponseStatus int
}

// BodyKeys returns the top-level JSON object keys of the request body, sorted.
// It returns nil for an empty body and an error for a body that is not a JSON
// object, which is what the wire-shape assertions need.
func (r Request) BodyKeys() ([]string, error) {
	if len(r.Body) == 0 {
		return nil, nil
	}
	var m map[string]json.RawMessage
	if err := json.Unmarshal(r.Body, &m); err != nil {
		return nil, fmt.Errorf("request %d body is not a JSON object: %w", r.Index, err)
	}
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys, nil
}

// BodyMap decodes the request body into a generic map.
func (r Request) BodyMap() (map[string]any, error) {
	var m map[string]any
	if err := json.Unmarshal(r.Body, &m); err != nil {
		return nil, fmt.Errorf("request %d body is not a JSON object: %w", r.Index, err)
	}
	return m, nil
}

// Scenario configures one run of the mock appliance.
type Scenario struct {
	// Username and Password are the credentials acquireToken accepts.
	Username string
	Password string
	// AuthSource, when non-empty, is additionally required on the acquireToken body.
	AuthSource string
	// Token is handed out by acquireToken and expected on later operations.
	Token string

	// ReportID is the identifier assigned to the created report.
	ReportID string
	// CreateStatus is the status createReport reports. It is deliberately
	// non-terminal: the caller has to poll to learn the outcome.
	CreateStatus string
	// PollStatuses are returned by successive getReport calls. The final entry
	// repeats if the caller keeps polling past the end of the slice.
	PollStatuses []string

	// DownloadBody and DownloadContentType are served by downloadReport once the
	// report has reached the contract's successful status.
	DownloadBody        []byte
	DownloadContentType string

	// OperationFailures makes a named operation return the supplied non-2xx
	// status. It is used to verify that clients stop and identify failures at
	// every stage of the flow.
	OperationFailures map[string]int
}

func (s *Scenario) withDefaults() {
	if s.Username == "" {
		s.Username = "svc-reporting"
	}
	if s.Password == "" {
		s.Password = "correct-horse-battery"
	}
	if s.Token == "" {
		s.Token = "0f0d3f1e-6a2b-4c8d-9e77-2b41f6c0a915::b7c4"
	}
	if s.ReportID == "" {
		s.ReportID = "6f1f0f8c-2b3c-4b7a-9d61-0c8e5a4f7d22"
	}
	if s.CreateStatus == "" {
		s.CreateStatus = "QUEUED"
	}
	if len(s.PollStatuses) == 0 {
		s.PollStatuses = []string{"QUEUED", "RUNNING", "COMPLETED"}
	}
	if s.DownloadBody == nil {
		s.DownloadBody = []byte("resource,metric,value\nvm-101,cpu|demand,42\n")
	}
	if s.DownloadContentType == "" {
		s.DownloadContentType = "text/csv"
	}
}

// Server is a running in-process mock.
type Server struct {
	contract *contract.Contract
	scenario Scenario
	client   *http.Client
	routes   []route

	mu       sync.Mutex
	requests []Request
	recorded chan struct{}
	polls    int
	lastPoll string
}

type route struct {
	op      contract.Operation
	matcher *regexpMatcher
}

// Start boots an in-process mock using the contract at docs/contract.json.
func Start(t *testing.T, sc Scenario) *Server {
	t.Helper()
	c, err := contract.LoadDefault()
	if err != nil {
		t.Fatalf("load contract: %v", err)
	}
	return StartWithContract(t, c, sc)
}

// StartWithContract boots a mock against an already-loaded contract.
func StartWithContract(t *testing.T, c *contract.Contract, sc Scenario) *Server {
	t.Helper()
	sc.withDefaults()

	s := &Server{contract: c, scenario: sc, recorded: make(chan struct{}, 128)}
	ids := make([]string, 0, len(c.Operations))
	for id := range c.Operations {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		op := c.Operations[id]
		s.routes = append(s.routes, route{op: op, matcher: newMatcher(c, op)})
	}

	s.client = &http.Client{Transport: roundTripperFunc(s.roundTrip)}
	return s
}

// URL is the mock's base URL. It carries no path, so a client is responsible
// for appending the contract's base path. Requests reach the mock through Client.
func (s *Server) URL() string { return "http://mockops.local" }

// Client returns an HTTP client whose transport dispatches requests directly to
// this mock without opening a network socket.
func (s *Server) Client() *http.Client { return s.client }

// Scenario returns the effective scenario, defaults applied.
func (s *Server) Scenario() Scenario { return s.scenario }

// Close is retained for compatibility; an in-process mock owns no socket.
func (s *Server) Close() {}

type roundTripperFunc func(*http.Request) (*http.Response, error)

func (f roundTripperFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func (s *Server) roundTrip(r *http.Request) (*http.Response, error) {
	select {
	case <-r.Context().Done():
		return nil, r.Context().Err()
	default:
	}
	recorder := httptest.NewRecorder()
	s.serve(recorder, r)
	resp := recorder.Result()
	resp.Request = r
	return resp, nil
}

// Requests returns a copy of the request log in arrival order.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.requests))
	for i, r := range s.requests {
		c := r
		c.Header = r.Header.Clone()
		c.Body = append([]byte(nil), r.Body...)
		c.Query = cloneValues(r.Query)
		c.PathParams = cloneMap(r.PathParams)
		out[i] = c
	}
	return out
}

// RequestsFor returns the recorded requests for one operationId, in order.
func (s *Server) RequestsFor(operationID string) []Request {
	var out []Request
	for _, r := range s.Requests() {
		if r.OperationID == operationID {
			out = append(out, r)
		}
	}
	return out
}

// RequestRecorded is notified after each request is appended to the log. The
// buffered notification lets synchronization-heavy tests avoid timing sleeps.
func (s *Server) RequestRecorded() <-chan struct{} { return s.recorded }

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	body := readBody(r)

	rec := Request{
		Method:   r.Method,
		Path:     r.URL.Path,
		RawQuery: r.URL.RawQuery,
		Query:    r.URL.Query(),
		Header:   r.Header.Clone(),
		Body:     body,
	}

	op, params, ok := s.match(r)
	if !ok {
		rec.ResponseStatus = http.StatusNotFound
		s.record(rec)
		writeJSON(w, http.StatusNotFound, map[string]any{
			"message": fmt.Sprintf("no contract operation serves %s %s", r.Method, r.URL.Path),
		})
		return
	}
	rec.OperationID = op.OperationID
	rec.PathParams = params

	status, payload, contentType := s.dispatch(op, rec)
	rec.ResponseStatus = status
	s.record(rec)

	if contentType == "" {
		writeJSON(w, status, payload)
		return
	}
	w.Header().Set("Content-Type", contentType)
	w.WriteHeader(status)
	if b, ok := payload.([]byte); ok {
		_, _ = w.Write(b)
	}
}

func (s *Server) match(r *http.Request) (contract.Operation, map[string]string, bool) {
	for _, rt := range s.routes {
		if !strings.EqualFold(rt.op.Method, r.Method) {
			continue
		}
		if params, ok := rt.matcher.match(r.URL.Path); ok {
			return rt.op, params, true
		}
	}
	return contract.Operation{}, nil, false
}

func (s *Server) dispatch(op contract.Operation, rec Request) (int, any, string) {
	if s.contract.RequiresAuth(op.OperationID) {
		want := s.contract.AuthHeaderValue(s.scenario.Token)
		if got := rec.Header.Get(s.contract.Authorization.HeaderName); got != want {
			return http.StatusUnauthorized, map[string]any{
				"message": "missing or malformed authorization header",
			}, ""
		}
	}
	if status := s.scenario.OperationFailures[op.OperationID]; status != 0 {
		return status, map[string]any{
			"message": fmt.Sprintf("forced %s failure", op.OperationID),
		}, ""
	}

	switch op.OperationID {
	case "acquireToken":
		return s.acquireToken(rec)
	case "createReport":
		return s.createReport(rec)
	case "getReport":
		return s.getReport(rec)
	case "downloadReport":
		return s.downloadReport(rec)
	default:
		// Unreachable while the contract and this switch agree; a contract that
		// names an operation the mock cannot serve is a wiring bug, not a 404.
		return http.StatusNotImplemented, map[string]any{
			"message": fmt.Sprintf("mock has no handler for contract operation %q", op.OperationID),
		}, ""
	}
}

func (s *Server) acquireToken(rec Request) (int, any, string) {
	var creds struct {
		Username   string `json:"username"`
		Password   string `json:"password"`
		AuthSource string `json:"authSource"`
	}
	if err := json.Unmarshal(rec.Body, &creds); err != nil {
		return http.StatusBadRequest, map[string]any{"message": "malformed credentials"}, ""
	}
	if creds.Username != s.scenario.Username || creds.Password != s.scenario.Password {
		return http.StatusUnauthorized, map[string]any{"message": "authentication failed"}, ""
	}
	if creds.AuthSource != s.scenario.AuthSource {
		return http.StatusUnauthorized, map[string]any{
			"message": fmt.Sprintf("unknown auth source %q", creds.AuthSource),
		}, ""
	}
	return http.StatusOK, map[string]any{
		"token":     s.scenario.Token,
		"validity":  1893456000000,
		"expiresAt": "2030-01-01T00:00:00Z",
		"roles":     []string{"ReportRunner"},
	}, ""
}

func (s *Server) createReport(rec Request) (int, any, string) {
	m, err := rec.BodyMap()
	if err != nil {
		return http.StatusBadRequest, map[string]any{"message": "malformed report body"}, ""
	}
	rb := s.contract.Operations["createReport"].RequestBody
	for _, name := range rb.RequiredProperties {
		v, ok := m[name].(string)
		if !ok || v == "" {
			return http.StatusBadRequest, map[string]any{
				"message": fmt.Sprintf("required property %q is missing or empty", name),
			}, ""
		}
	}
	allowed := map[string]bool{}
	for _, name := range rb.AllowedProperties {
		allowed[name] = true
	}
	for name := range m {
		if !allowed[name] {
			return http.StatusBadRequest, map[string]any{
				"message": fmt.Sprintf("property %q is server-populated and must not be sent", name),
			}, ""
		}
	}

	s.mu.Lock()
	s.polls = 0
	s.lastPoll = ""
	s.mu.Unlock()

	out := map[string]any{
		"id":                 s.scenario.ReportID,
		"status":             s.scenario.CreateStatus,
		"reportDefinitionId": m["reportDefinitionId"],
		"resourceId":         m["resourceId"],
		"owner":              s.scenario.Username,
	}
	if name, ok := m["name"]; ok {
		out["name"] = name
	}
	return http.StatusOK, out, ""
}

func (s *Server) getReport(rec Request) (int, any, string) {
	if rec.PathParams["id"] != s.scenario.ReportID {
		return http.StatusNotFound, map[string]any{
			"message": fmt.Sprintf("no report %q", rec.PathParams["id"]),
		}, ""
	}

	s.mu.Lock()
	idx := s.polls
	if idx >= len(s.scenario.PollStatuses) {
		idx = len(s.scenario.PollStatuses) - 1
	}
	status := s.scenario.PollStatuses[idx]
	s.polls++
	s.lastPoll = status
	s.mu.Unlock()

	out := map[string]any{
		"id":                 s.scenario.ReportID,
		"status":             status,
		"reportDefinitionId": "8b1c3a12-4f6d-4a1e-9c33-7d5e2b0a91f4",
		"resourceId":         "2a7d5e90-1c4b-4e33-8f21-6b9a0d3c5e18",
		"owner":              s.scenario.Username,
	}
	if s.contract.IsTerminal(status) {
		out["completionTime"] = "2026-05-20T11:04:52Z"
	}
	return http.StatusOK, out, ""
}

func (s *Server) downloadReport(rec Request) (int, any, string) {
	if rec.PathParams["id"] != s.scenario.ReportID {
		return http.StatusNotFound, map[string]any{
			"message": fmt.Sprintf("no report %q", rec.PathParams["id"]),
		}, ""
	}

	s.mu.Lock()
	last := s.lastPoll
	s.mu.Unlock()

	if last != s.contract.ReportStatus.Successful {
		return http.StatusConflict, map[string]any{
			"message": fmt.Sprintf("report is %q, not %q; it cannot be downloaded yet",
				last, s.contract.ReportStatus.Successful),
		}, ""
	}

	ct := s.scenario.DownloadContentType
	if f := rec.Query.Get("format"); f != "" {
		switch strings.ToLower(f) {
		case "csv":
			ct = "text/csv"
		case "pdf":
			ct = "application/pdf"
		default:
			return http.StatusBadRequest, map[string]any{
				"message": fmt.Sprintf("unsupported format %q", f),
			}, ""
		}
	}
	return http.StatusOK, s.scenario.DownloadBody, ct
}

func (s *Server) record(rec Request) {
	s.mu.Lock()
	rec.Index = len(s.requests)
	s.requests = append(s.requests, rec)
	s.mu.Unlock()
	select {
	case s.recorded <- struct{}{}:
	default:
	}
}
