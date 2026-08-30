// Package mockserver provides a loopback HTTP stand-in for SDDC Manager,
// pinned to the wire contract in docs/contract.json.
//
// It serves only the three operations the contract names
// (postHostsPrechecks_1, getHostsPrechecksResponse, commissionHosts). Any other
// method/path pair is recorded and answered 404, so a client that invents an
// endpoint fails loudly rather than silently succeeding.
//
// Every request is appended to a request log that tests can read back with
// Requests. The log holds the raw body bytes exactly as they arrived, which is
// what lets a test assert the serialized wire shape rather than the shape of
// the Go value that produced it.
//
// No VMware endpoint is contacted. The server listens on loopback only.
package mockserver

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"

	hostcommission "vcf90.local/hostcommission"
)

// Recorded is one observed HTTP request.
type Recorded struct {
	Method      string
	Path        string
	ContentType string
	// Body is the raw request body, byte for byte as received.
	Body []byte
	// Matched is the operationId this request was routed to, or "" if the
	// request matched no operation the contract names.
	Matched string
	// Status is the response status the mock returned.
	Status int
}

// BodyMap decodes the recorded body as a JSON object.
func (r Recorded) BodyMap() (map[string]any, error) {
	var m map[string]any
	if err := json.Unmarshal(r.Body, &m); err != nil {
		return nil, err
	}
	return m, nil
}

// BodyArray decodes the recorded body as a bare JSON array.
func (r Recorded) BodyArray() ([]any, error) {
	var a []any
	if err := json.Unmarshal(r.Body, &a); err != nil {
		return nil, err
	}
	return a, nil
}

// HostPrecheckOutcome is the per-host precheck verdict the mock reports.
type HostPrecheckOutcome struct {
	// FQDN identifies the host this outcome belongs to.
	FQDN string
	// Result is "SUCCEEDED" or "FAILED".
	Result string
	// Error is the precheck error message, set when Result is "FAILED".
	Error string
}

// Config drives the scenario the mock plays out.
type Config struct {
	// PrecheckID is the execution id returned by POST /v1/hosts/prechecks.
	// Defaults to "precheck-0001".
	PrecheckID string
	// PrecheckResult is the overall result once the precheck completes:
	// "SUCCEEDED" or "FAILED". Defaults to "SUCCEEDED".
	PrecheckResult string
	// InProgressPolls is how many GET /v1/hosts/prechecks/{id} calls report
	// executionStatus IN_PROGRESS before the mock switches to COMPLETED. Zero
	// means the first poll already reports COMPLETED.
	InProgressPolls int
	// HostOutcomes is the per-host detail reported once the precheck
	// completes. When empty the mock derives one entry per submitted host from
	// PrecheckResult.
	HostOutcomes []HostPrecheckOutcome
	// TaskID is the id of the Task returned by POST /v1/hosts. Defaults to
	// "task-0001".
	TaskID string
	// PrecheckSubmitStatus overrides the status code returned by POST
	// /v1/hosts/prechecks. Defaults to the contract's success status (200).
	PrecheckSubmitStatus int
	// PrecheckStatusStatus overrides the status code returned by GET
	// /v1/hosts/prechecks/{id}. Defaults to the contract's success status (200).
	PrecheckStatusStatus int
	// CommissionStatus overrides the status code returned by POST /v1/hosts.
	// Defaults to the contract's success status (202).
	CommissionStatus int
}

func (c *Config) applyDefaults() {
	if c.PrecheckID == "" {
		c.PrecheckID = "precheck-0001"
	}
	if c.PrecheckResult == "" {
		c.PrecheckResult = "SUCCEEDED"
	}
	if c.TaskID == "" {
		c.TaskID = "task-0001"
	}
}

// Server is a loopback SDDC Manager stand-in.
type Server struct {
	ts       *httptest.Server
	contract *hostcommission.Contract
	client   *http.Client
	url      string

	mu           sync.Mutex
	cfg          Config
	log          []Recorded
	polls        int
	submitted    []string // FQDNs seen by the precheck submission
	precheckSeen bool
}

// New starts a mock on loopback with the given scenario. Close it when done.
func New(cfg Config) *Server {
	cfg.applyDefaults()
	s := &Server{
		contract: hostcommission.MustLoad(),
		cfg:      cfg,
	}

	// Prefer a real IPv4 loopback server. Some verification sandboxes disable
	// sockets altogether, so fall back to an in-process RoundTripper that still
	// executes genuine HTTP requests through the exact same handler and request
	// log. This keeps the verifier deterministic without weakening wire checks.
	ln, err := net.Listen("tcp4", "127.0.0.1:0")
	if err == nil {
		s.ts = httptest.NewUnstartedServer(http.HandlerFunc(s.handle))
		s.ts.Listener = ln
		s.ts.Start()
		s.client = s.ts.Client()
		s.url = s.ts.URL
	} else {
		s.url = "http://127.0.0.1"
		s.client = &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
			if err := req.Context().Err(); err != nil {
				return nil, err
			}
			recorder := httptest.NewRecorder()
			s.handle(recorder, req)
			resp := recorder.Result()
			resp.Request = req
			return resp, nil
		})}
	}
	return s
}

// URL is the base URL of the running mock, e.g. "http://127.0.0.1:39481".
func (s *Server) URL() string { return s.url }

// Client returns an HTTP client wired to this server. In socket-restricted
// sandboxes it uses an in-process transport; otherwise it is the httptest
// server's ordinary loopback client.
func (s *Server) Client() *http.Client { return s.client }

// Close shuts the mock down.
func (s *Server) Close() {
	if s.ts != nil {
		s.ts.Close()
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

// Requests returns a copy of the request log in arrival order.
func (s *Server) Requests() []Recorded {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Recorded, len(s.log))
	copy(out, s.log)
	return out
}

// RequestsFor returns the logged requests routed to the given operationId.
func (s *Server) RequestsFor(operationID string) []Recorded {
	var out []Recorded
	for _, r := range s.Requests() {
		if r.Matched == operationID {
			out = append(out, r)
		}
	}
	return out
}

// Count returns how many logged requests were routed to the given operationId.
func (s *Server) Count(operationID string) int { return len(s.RequestsFor(operationID)) }

// Unmatched returns logged requests that matched no contract operation.
func (s *Server) Unmatched() []Recorded {
	var out []Recorded
	for _, r := range s.Requests() {
		if r.Matched == "" {
			out = append(out, r)
		}
	}
	return out
}

// record appends r to the log and returns its index. An index, rather than a
// pointer into the slice, stays valid when a concurrent request grows the log.
func (s *Server) record(r Recorded) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log = append(s.log, r)
	return len(s.log) - 1
}

func (s *Server) setStatus(idx, status int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log[idx].Status = status
}

func (s *Server) matchedAt(idx int) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.log[idx].Matched
}

func (s *Server) handle(w http.ResponseWriter, r *http.Request) {
	var body []byte
	if r.Body != nil {
		body, _ = io.ReadAll(r.Body)
		_ = r.Body.Close()
	}

	idx := s.record(Recorded{
		Method:      r.Method,
		Path:        r.URL.Path,
		ContentType: r.Header.Get("Content-Type"),
		Body:        body,
		Matched:     s.route(r.Method, r.URL.Path),
	})

	switch s.matchedAt(idx) {
	case "postHostsPrechecks_1":
		s.handlePostPrechecks(w, idx, body)
	case "getHostsPrechecksResponse":
		s.handleGetPrechecks(w, idx, r.URL.Path)
	case "commissionHosts":
		s.handleCommission(w, idx, body)
	default:
		s.writeError(w, idx, http.StatusNotFound,
			fmt.Sprintf("no operation in the 9.0 contract serves %s %s", r.Method, r.URL.Path))
	}
}

// route maps a method/path pair to the operationId the contract names, or ""
// when the contract names no such operation.
func (s *Server) route(method, path string) string {
	for id, op := range s.contract.Operations {
		if !strings.EqualFold(op.Method, method) {
			continue
		}
		if matchPath(op.Path, path) {
			return id
		}
	}
	return ""
}

// matchPath compares a request path against a contract path template, treating
// a {name} segment as a single-segment wildcard that must be non-empty.
func matchPath(template, path string) bool {
	t := strings.Split(strings.Trim(template, "/"), "/")
	p := strings.Split(strings.Trim(path, "/"), "/")
	if len(t) != len(p) {
		return false
	}
	for i := range t {
		if strings.HasPrefix(t[i], "{") && strings.HasSuffix(t[i], "}") {
			if p[i] == "" {
				return false
			}
			continue
		}
		if t[i] != p[i] {
			return false
		}
	}
	return true
}

func (s *Server) handlePostPrechecks(w http.ResponseWriter, idx int, body []byte) {
	// The contract says this body is an object with a "hosts" array, not a
	// bare array. Enforce that here so a mis-shaped submission cannot pass.
	var req struct {
		Hosts []map[string]any `json:"hosts"`
	}
	if err := json.Unmarshal(body, &req); err != nil {
		s.writeError(w, idx, http.StatusBadRequest,
			"postHostsPrechecks_1 expects a JSON object with a 'hosts' array; got "+shapeOf(body))
		return
	}
	if req.Hosts == nil {
		s.writeError(w, idx, http.StatusBadRequest,
			"postHostsPrechecks_1 request body is missing the 'hosts' array")
		return
	}
	if len(req.Hosts) == 0 {
		s.writeError(w, idx, http.StatusBadRequest, "'hosts' must not be empty")
		return
	}

	var fqdns []string
	for i, h := range req.Hosts {
		if err := s.validateHost(h); err != nil {
			s.writeError(w, idx, http.StatusBadRequest, fmt.Sprintf("hosts[%d]: %v", i, err))
			return
		}
		fqdns = append(fqdns, fmt.Sprint(h["fqdn"]))
	}

	s.mu.Lock()
	s.submitted = fqdns
	s.precheckSeen = true
	s.polls = 0
	s.mu.Unlock()

	status := s.cfg.PrecheckSubmitStatus
	if status == 0 {
		status = s.contract.MustOp("postHostsPrechecks_1").SuccessStatus
	}
	s.writeJSON(w, idx, status, map[string]any{
		"id":              s.cfg.PrecheckID,
		"executionStatus": "IN_PROGRESS",
		"result":          "",
	})
}

func (s *Server) handleGetPrechecks(w http.ResponseWriter, idx int, path string) {
	segs := strings.Split(strings.Trim(path, "/"), "/")
	id := segs[len(segs)-1]

	s.mu.Lock()
	seen := s.precheckSeen
	if !seen {
		s.mu.Unlock()
		s.writeError(w, idx, http.StatusNotFound, "no prechecks have been submitted")
		return
	}
	if id != s.cfg.PrecheckID {
		s.mu.Unlock()
		s.writeError(w, idx, http.StatusNotFound, fmt.Sprintf("unknown precheck id %q", id))
		return
	}
	s.polls++
	done := s.polls > s.cfg.InProgressPolls
	s.mu.Unlock()
	status := s.cfg.PrecheckStatusStatus
	if status == 0 {
		status = s.contract.MustOp("getHostsPrechecksResponse").SuccessStatus
	}

	if !done {
		s.writeJSON(w, idx, status, map[string]any{
			"id":              s.cfg.PrecheckID,
			"executionStatus": "IN_PROGRESS",
			"result":          "",
		})
		return
	}

	s.writeJSON(w, idx, status, map[string]any{
		"id":              s.cfg.PrecheckID,
		"executionStatus": "COMPLETED",
		"result":          s.cfg.PrecheckResult,
		"hostPrechecks":   s.hostPrechecks(),
	})
}

func (s *Server) hostPrechecks() []map[string]any {
	s.mu.Lock()
	outcomes := s.cfg.HostOutcomes
	submitted := append([]string(nil), s.submitted...)
	overall := s.cfg.PrecheckResult
	s.mu.Unlock()

	if len(outcomes) == 0 {
		for _, f := range submitted {
			o := HostPrecheckOutcome{FQDN: f, Result: overall}
			if overall == "FAILED" {
				o.Error = "precheck failed for " + f
			}
			outcomes = append(outcomes, o)
		}
	}

	out := make([]map[string]any, 0, len(outcomes))
	for _, o := range outcomes {
		hp := map[string]any{
			"hostPrecheckDetails": map[string]any{"fqdn": o.FQDN},
			"result":              o.Result,
		}
		if o.Error != "" {
			hp["error"] = o.Error
		}
		out = append(out, hp)
	}
	return out
}

func (s *Server) handleCommission(w http.ResponseWriter, idx int, body []byte) {
	// The contract says this body is a BARE array of HostCommissionSpec.
	var specs []map[string]any
	if err := json.Unmarshal(body, &specs); err != nil {
		s.writeError(w, idx, http.StatusBadRequest,
			"commissionHosts expects a bare JSON array of HostCommissionSpec; got "+shapeOf(body))
		return
	}
	if len(specs) == 0 {
		s.writeError(w, idx, http.StatusBadRequest, "commissionHosts body must not be empty")
		return
	}
	for i, h := range specs {
		if err := s.validateHost(h); err != nil {
			s.writeError(w, idx, http.StatusBadRequest, fmt.Sprintf("[%d]: %v", i, err))
			return
		}
	}

	status := s.cfg.CommissionStatus
	if status == 0 {
		status = s.contract.MustOp("commissionHosts").SuccessStatus
	}
	s.writeJSON(w, idx, status, map[string]any{
		"id":                s.cfg.TaskID,
		"name":              "Commissioning hosts",
		"type":              "HOST_COMMISSION",
		"status":            "IN_PROGRESS",
		"creationTimestamp": "2025-01-01T00:00:00.000Z",
	})
}

// validateHost enforces the HostCommissionSpec rules the contract states:
// required properties present and non-empty, no unknown properties, and a
// storageType this release accepts.
func (s *Server) validateHost(h map[string]any) error {
	schema := s.contract.Schemas["HostCommissionSpec"]

	known := map[string]bool{}
	for _, k := range schema.Required {
		known[k] = true
	}
	for _, k := range schema.Optional {
		known[k] = true
	}

	for _, req := range schema.Required {
		v, ok := h[req]
		if !ok {
			return fmt.Errorf("missing required property %q", req)
		}
		if str, isStr := v.(string); isStr && str == "" {
			return fmt.Errorf("required property %q must not be empty", req)
		}
	}
	for k := range h {
		if !known[k] {
			return fmt.Errorf("unknown property %q is not part of HostCommissionSpec at 9.0.0.0", k)
		}
	}

	st, _ := h["storageType"].(string)
	if !s.contract.StorageTypeAllowed(st) {
		return fmt.Errorf("storageType %q is not accepted at 9.0.0.0 (allowed: %s)",
			st, strings.Join(s.contract.AllowedStorageTypes(), ", "))
	}
	return nil
}

func (s *Server) writeJSON(w http.ResponseWriter, idx, status int, payload any) {
	s.setStatus(idx, status)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func (s *Server) writeError(w http.ResponseWriter, idx, status int, msg string) {
	s.writeJSON(w, idx, status, map[string]any{
		"errorCode": fmt.Sprintf("MOCK_%d", status),
		"errorType": "MOCK_CONTRACT_VIOLATION",
		"message":   msg,
	})
}

func shapeOf(body []byte) string {
	t := strings.TrimSpace(string(body))
	switch {
	case t == "":
		return "an empty body"
	case strings.HasPrefix(t, "["):
		return "a bare array"
	case strings.HasPrefix(t, "{"):
		return "an object"
	default:
		return "a non-JSON body"
	}
}
