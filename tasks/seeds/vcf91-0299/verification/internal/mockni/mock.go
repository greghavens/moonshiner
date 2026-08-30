// Package mockni provides a loopback stand-in for the VCF Operations for
// Networks 9.1 API, pinned to docs/contract.json.
//
// It serves ONLY the three operations the contract names:
//
//	addApplication        POST /api/ni/groups/applications
//	addTier               POST /api/ni/groups/applications/{id}/tiers
//	listApplicationTiers  GET  /api/ni/groups/applications/{id}/tiers
//
// Any other method/path combination is answered with 404 and recorded, so a
// test can prove the client never reached for an off-contract operation.
//
// Every inbound request is appended to a request log before any validation
// runs, so the log always reflects what actually went on the wire.
//
// Requests are served in-process by an http.RoundTripper. No network socket is
// opened, so the fixture works in verification sandboxes that prohibit network
// access while still exercising the client's real HTTP request/response path.
package mockni

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
)

// RecordedRequest is one observed inbound HTTP request.
type RecordedRequest struct {
	Method      string
	Path        string
	RawQuery    string
	Header      http.Header
	Body        []byte
	OperationID string // contract operationId, or "" when off-contract
}

// DecodeBody unmarshals the recorded JSON body into a generic map. It reports
// an error when the body is absent or is not a JSON object.
func (r RecordedRequest) DecodeBody() (map[string]any, error) {
	if len(r.Body) == 0 {
		return nil, fmt.Errorf("mockni: %s %s had an empty body", r.Method, r.Path)
	}
	var m map[string]any
	dec := json.NewDecoder(strings.NewReader(string(r.Body)))
	dec.UseNumber()
	if err := dec.Decode(&m); err != nil {
		return nil, fmt.Errorf("mockni: %s %s body is not a JSON object: %w", r.Method, r.Path, err)
	}
	return m, nil
}

// Rejection makes addTier fail for a named tier, so a scenario can place a
// failure at a chosen step of a multi-step change.
type Rejection struct {
	Status  int    // HTTP status to return; must be one of the contract's addTier errors
	Code    int    // ApiError.code
	Message string // ApiError.message
}

// Config configures a Server.
type Config struct {
	// Token is the bare token. The server requires the request to carry
	// "Authorization: NetworkInsight <Token>", per the spec's ApiKeyAuth scheme.
	Token string

	// RejectTiers maps a tier name to the failure addTier should return for it.
	RejectTiers map[string]Rejection

	// AcknowledgeWithoutCommit makes addTier return its normal 201 response for
	// a named tier without adding that tier to committed state. It models the
	// stale per-call bookkeeping that listApplicationTiers must correct after a
	// later rollout step fails.
	AcknowledgeWithoutCommit map[string]bool
}

type tierState struct {
	entityID string
	name     string
	body     map[string]any
}

type appState struct {
	entityID string
	name     string
	tiers    []*tierState
}

// Server is a running loopback mock.
type Server struct {
	mu       sync.Mutex
	requests []RecordedRequest
	apps     map[string]*appState
	byName   map[string]*appState
	appOrder []*appState
	appSeq   int
	tierSeq  int
	cfg      Config
}

// New starts a Server on the loopback interface.
func New(cfg Config) *Server {
	return &Server{
		apps:   make(map[string]*appState),
		byName: make(map[string]*appState),
		cfg:    cfg,
	}
}

// URL is the base URL understood by Client. The API base path "/api/ni" is
// NOT included. Client's transport handles this address entirely in-process.
func (s *Server) URL() string { return "http://127.0.0.1" }

// Client returns an *http.Client configured to reach this server.
func (s *Server) Client() *http.Client {
	return &http.Client{Transport: roundTripperFunc(func(r *http.Request) (*http.Response, error) {
		recorder := httptest.NewRecorder()
		s.serve(recorder, r)
		resp := recorder.Result()
		resp.Request = r
		return resp, nil
	})}
}

// Close is present for parity with a network-backed test server.
func (s *Server) Close() {}

type roundTripperFunc func(*http.Request) (*http.Response, error)

func (f roundTripperFunc) RoundTrip(r *http.Request) (*http.Response, error) {
	return f(r)
}

// Requests returns a copy of the request log, in arrival order.
func (s *Server) Requests() []RecordedRequest {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]RecordedRequest, len(s.requests))
	copy(out, s.requests)
	return out
}

// RequestsFor returns the recorded requests for one contract operationId.
func (s *Server) RequestsFor(operationID string) []RecordedRequest {
	var out []RecordedRequest
	for _, r := range s.Requests() {
		if r.OperationID == operationID {
			out = append(out, r)
		}
	}
	return out
}

// TierNames returns the names of the tiers actually created under an
// application, in creation order. It reflects committed server state, which a
// test can compare against a client's report.
func (s *Server) TierNames(appEntityID string) []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	app := s.apps[appEntityID]
	if app == nil || len(app.tiers) == 0 {
		return nil
	}
	out := make([]string, 0, len(app.tiers))
	for _, t := range app.tiers {
		out = append(out, t.name)
	}
	return out
}

// ApplicationNames returns the names of the applications actually created, in
// creation order.
func (s *Server) ApplicationNames() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]string, 0, len(s.appOrder))
	for _, a := range s.appOrder {
		out = append(out, a.name)
	}
	return out
}

func appEntityID(n int) string  { return fmt.Sprintf("18230:561:%d", 271275765+n) }
func tierEntityID(n int) string { return fmt.Sprintf("18230:562:%d", 1266458745+n) }

// route resolves a method+path to a contract operationId and its path params.
func route(method, path string) (operationID string, id string, ok bool) {
	const base = "/api/ni/groups/applications"
	switch {
	case method == http.MethodPost && path == base:
		return "addApplication", "", true
	case strings.HasPrefix(path, base+"/") && strings.HasSuffix(path, "/tiers"):
		id = strings.TrimSuffix(strings.TrimPrefix(path, base+"/"), "/tiers")
		if id == "" || strings.Contains(id, "/") {
			return "", "", false
		}
		switch method {
		case http.MethodPost:
			return "addTier", id, true
		case http.MethodGet:
			return "listApplicationTiers", id, true
		}
	}
	return "", "", false
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	var body []byte
	if r.Body != nil {
		body, _ = io.ReadAll(r.Body)
		_ = r.Body.Close()
	}

	opID, id, known := route(r.Method, r.URL.Path)

	s.mu.Lock()
	s.requests = append(s.requests, RecordedRequest{
		Method:      r.Method,
		Path:        r.URL.Path,
		RawQuery:    r.URL.RawQuery,
		Header:      r.Header.Clone(),
		Body:        append([]byte(nil), body...),
		OperationID: opID,
	})
	s.mu.Unlock()

	if !known {
		writeErr(w, http.StatusNotFound, 404,
			fmt.Sprintf("no such operation: %s %s is not named by docs/contract.json", r.Method, r.URL.Path))
		return
	}

	if got, want := r.Header.Get("Authorization"), "NetworkInsight "+s.cfg.Token; got != want {
		writeErr(w, http.StatusUnauthorized, 401, "unauthorized")
		return
	}

	switch opID {
	case "addApplication":
		s.addApplication(w, r, body)
	case "addTier":
		s.addTier(w, r, id, body)
	case "listApplicationTiers":
		s.listApplicationTiers(w, id)
	}
}

func (s *Server) addApplication(w http.ResponseWriter, r *http.Request, body []byte) {
	if ct := r.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		writeErr(w, http.StatusBadRequest, 400, "Content-Type must be application/json")
		return
	}
	var req map[string]any
	if err := json.Unmarshal(body, &req); err != nil {
		writeErr(w, http.StatusBadRequest, 400, "malformed JSON body")
		return
	}
	name, _ := req["name"].(string)
	if name == "" {
		writeErr(w, http.StatusBadRequest, 400, "ApplicationRequest.name is required")
		return
	}

	s.mu.Lock()
	if _, dup := s.byName[name]; dup {
		s.mu.Unlock()
		writeErr(w, http.StatusBadRequest, 400, "an application named "+name+" already exists")
		return
	}
	s.appSeq++
	app := &appState{entityID: appEntityID(s.appSeq), name: name}
	s.apps[app.entityID] = app
	s.byName[name] = app
	s.appOrder = append(s.appOrder, app)
	s.mu.Unlock()

	writeJSON(w, http.StatusCreated, map[string]any{
		"entity_id":   app.entityID,
		"name":        app.name,
		"entity_type": "Application",
	})
}

func (s *Server) addTier(w http.ResponseWriter, r *http.Request, appID string, body []byte) {
	if ct := r.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		writeErr(w, http.StatusBadRequest, 400, "Content-Type must be application/json")
		return
	}

	s.mu.Lock()
	app := s.apps[appID]
	s.mu.Unlock()
	if app == nil {
		writeErr(w, http.StatusNotFound, 404, "no application with entity_id "+appID)
		return
	}

	var req map[string]any
	if err := json.Unmarshal(body, &req); err != nil {
		writeErr(w, http.StatusBadRequest, 400, "malformed JSON body")
		return
	}
	name, _ := req["name"].(string)

	if rej, ok := s.cfg.RejectTiers[name]; ok {
		writeErr(w, rej.Status, rej.Code, rej.Message)
		return
	}

	s.mu.Lock()
	s.tierSeq++
	t := &tierState{entityID: tierEntityID(s.tierSeq), name: name, body: req}
	if !s.cfg.AcknowledgeWithoutCommit[name] {
		app.tiers = append(app.tiers, t)
	}
	s.mu.Unlock()

	writeJSON(w, http.StatusCreated, tierJSON(t, app))
}

func (s *Server) listApplicationTiers(w http.ResponseWriter, appID string) {
	s.mu.Lock()
	app := s.apps[appID]
	var results []any
	if app != nil {
		for _, t := range app.tiers {
			results = append(results, tierJSON(t, app))
		}
	}
	s.mu.Unlock()

	if app == nil {
		writeErr(w, http.StatusNotFound, 404, "no application with entity_id "+appID)
		return
	}
	if results == nil {
		results = []any{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"results": results})
}

func tierJSON(t *tierState, app *appState) map[string]any {
	out := map[string]any{
		"entity_id":   t.entityID,
		"name":        t.name,
		"entity_type": "Tier",
		"application": map[string]any{
			"entity_id":   app.entityID,
			"entity_type": "Application",
		},
	}
	if c, ok := t.body["group_membership_criteria"]; ok {
		out["group_membership_criteria"] = c
	}
	return out
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, status, code int, msg string) {
	writeJSON(w, status, map[string]any{"code": code, "message": msg})
}
