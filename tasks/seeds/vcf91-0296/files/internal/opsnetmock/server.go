// Package opsnetmock is a loopback HTTP mock of the VCF Operations for Networks
// operations named in docs/contract.json.
//
// It serves exactly four operations - create, delete, listApplications and
// getApplicationById - under the base path /api/ni. Any other path answers 404
// and any unsupported method on a contract path answers 405, so a client that
// drifts off the contract fails loudly instead of silently passing.
//
// The mock listens on 127.0.0.1 only. It never contacts a VMware endpoint.
//
// Every request is appended to an in-memory request log that tests can read via
// Log(). The log records the resolved operationId, the query parameters that
// were actually present on the wire, the raw request body and the status the
// mock answered with, which is what lets a test assert that unset optional
// fields were omitted rather than sent empty.
//
// This file is part of the protected harness. Do not edit it.
package opsnetmock

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// BasePath is the servers[0].url value from the specification.
const BasePath = "/api/ni"

// AuthPrefix is the Authorization header prefix required by the ApiKeyAuth
// security scheme ("API Key - NetworkInsight {token}").
const AuthPrefix = "NetworkInsight "

// Route is one operation the mock serves.
type Route struct {
	OperationID string
	Method      string
	// Path is the specification path, relative to BasePath, still templated.
	Path string
}

// ContractOperations returns the operations the mock serves, keyed the same way
// docs/contract.json keys them. The verifier asserts that this set is exactly
// the set in the contract file.
func ContractOperations() []Route {
	return []Route{
		{OperationID: "create", Method: http.MethodPost, Path: "/auth/token"},
		{OperationID: "delete", Method: http.MethodDelete, Path: "/auth/token"},
		{OperationID: "listApplications", Method: http.MethodGet, Path: "/groups/applications"},
		{OperationID: "getApplicationById", Method: http.MethodGet, Path: "/groups/applications/{id}"},
	}
}

// Entry is one recorded request.
type Entry struct {
	// Seq is the 1-based arrival order.
	Seq int
	// OperationID is the resolved contract operationId, or "" when the request
	// did not match any contract operation.
	OperationID string
	Method      string
	// Path is the full request path including BasePath.
	Path string
	// Query holds only the parameters that were present on the wire.
	Query url.Values
	// Authorization is the raw Authorization header ("" when absent).
	Authorization string
	ContentType   string
	// Body is the raw request body bytes.
	Body []byte
	// Status is the HTTP status the mock answered with.
	Status int
	// ResponseBody is the raw response body the mock sent, so a rejection
	// message is visible in a test failure.
	ResponseBody []byte
	// TokenIndex is the 1-based index of the token the Authorization header
	// referred to, or 0 when there was no header or the token is unknown.
	TokenIndex int
}

// QueryKeys returns the sorted names of the query parameters that were present.
func (e Entry) QueryKeys() []string {
	keys := make([]string, 0, len(e.Query))
	for k := range e.Query {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

// Options configures a Server.
type Options struct {
	// Now supplies the mock's clock. Defaults to time.Now. Tests share a fake
	// clock between the mock and the client under test so that token expiry is
	// deterministic.
	Now func() time.Time

	// Applications is the number of application entities in the fixture
	// inventory. Defaults to 23.
	Applications int

	// DefaultPageSize is used when a listApplications request omits "size".
	// Defaults to 10, the default declared in the specification.
	DefaultPageSize int

	// TokenTTL returns the lifetime of the index-th issued token (1-based).
	// nil means 24h for every token.
	TokenTTL func(index int) time.Duration

	// RequestBudget returns how many authenticated requests the index-th issued
	// token (1-based) will serve before the mock answers 401 for it, regardless
	// of the clock. A negative return means unlimited. nil means unlimited for
	// every token.
	RequestBudget func(index int) int

	// RejectCredentials makes operationId "create" answer 401.
	RejectCredentials bool

	// MaxRequests bounds the whole run: once the mock has served this many
	// requests it answers 503 for everything. This is a harness backstop, not
	// something the specification describes - it stops a client that retries
	// without limit from hammering the mock until the test times out. Defaults
	// to 500; a correct full collection needs about 30.
	MaxRequests int
}

type tokenState struct {
	value   string
	expiry  time.Time
	budget  int // remaining authenticated requests; negative means unlimited
	revoked bool
}

type application struct {
	EntityID     string
	Name         string
	TierCount    int
	MemberCount  int
	UpdateStatus string
}

// Server is a running loopback mock.
type Server struct {
	opts Options
	http *httptest.Server
	apps []application

	mu           sync.Mutex
	log          []Entry
	tokens       []*tokenState
	unauthorized int
}

// New starts a mock on 127.0.0.1 and returns it. Call Close when done.
func New(opts Options) *Server {
	if opts.Now == nil {
		opts.Now = time.Now
	}
	if opts.Applications <= 0 {
		opts.Applications = 23
	}
	if opts.DefaultPageSize <= 0 {
		opts.DefaultPageSize = 10
	}
	if opts.MaxRequests <= 0 {
		opts.MaxRequests = 500
	}
	s := &Server{opts: opts}
	s.apps = buildFixture(opts.Applications)
	s.http = httptest.NewServer(http.HandlerFunc(s.serve))
	return s
}

// URL is the loopback origin of the mock, with no path component. Callers are
// expected to append BasePath themselves, the way they would against a real
// appliance.
func (s *Server) URL() string { return s.http.URL }

// Close shuts the mock down.
func (s *Server) Close() { s.http.Close() }

// Log returns a snapshot copy of the request log.
func (s *Server) Log() []Entry {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Entry, len(s.log))
	copy(out, s.log)
	return out
}

// OperationCounts counts log entries per resolved operationId.
func (s *Server) OperationCounts() map[string]int {
	counts := map[string]int{}
	for _, e := range s.Log() {
		counts[e.OperationID]++
	}
	return counts
}

// TokensIssued is the number of tokens operationId "create" handed out.
func (s *Server) TokensIssued() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.tokens)
}

// Unauthorized is the number of 401 responses the mock served.
func (s *Server) Unauthorized() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.unauthorized
}

// TokenValue returns the value of the index-th issued token (1-based).
func (s *Server) TokenValue(index int) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if index < 1 || index > len(s.tokens) {
		return ""
	}
	return s.tokens[index-1].value
}

// EntityIDs returns the fixture entity IDs in inventory order.
func (s *Server) EntityIDs() []string {
	out := make([]string, len(s.apps))
	for i, a := range s.apps {
		out[i] = a.EntityID
	}
	return out
}

// NameFor returns the fixture name of an entity ID.
func (s *Server) NameFor(entityID string) string {
	for _, a := range s.apps {
		if a.EntityID == entityID {
			return a.Name
		}
	}
	return ""
}

func buildFixture(n int) []application {
	statuses := []string{"NO_CHANGE", "UPDATED", "ADDED", "NO_CHANGE"}
	apps := make([]application, n)
	for i := range apps {
		apps[i] = application{
			EntityID:     fmt.Sprintf("18230:561:%09d", 271275765+i),
			Name:         fmt.Sprintf("app-%02d", i+1),
			TierCount:    2 + i%4,
			MemberCount:  10 + 5*i,
			UpdateStatus: statuses[i%len(statuses)],
		}
	}
	return apps
}

// recorder wraps the response so the mock can record the status it sent.
type recorder struct {
	http.ResponseWriter
	status int
	body   []byte
}

func (r *recorder) WriteHeader(code int) {
	if r.status == 0 {
		r.status = code
	}
	r.ResponseWriter.WriteHeader(code)
}

func (r *recorder) Write(b []byte) (int, error) {
	if r.status == 0 {
		r.status = http.StatusOK
	}
	r.body = append(r.body, b...)
	return r.ResponseWriter.Write(b)
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	body := readAll(r)
	rec := &recorder{ResponseWriter: w}

	entry := Entry{
		Method:        r.Method,
		Path:          r.URL.Path,
		Query:         r.URL.Query(),
		Authorization: r.Header.Get("Authorization"),
		ContentType:   r.Header.Get("Content-Type"),
		Body:          body,
	}

	opID, tokenIndex := s.dispatch(rec, r, body)

	entry.OperationID = opID
	entry.TokenIndex = tokenIndex
	entry.ResponseBody = rec.body
	entry.Status = rec.status
	if entry.Status == 0 {
		entry.Status = http.StatusOK
	}

	s.mu.Lock()
	entry.Seq = len(s.log) + 1
	s.log = append(s.log, entry)
	if entry.Status == http.StatusUnauthorized {
		s.unauthorized++
	}
	s.mu.Unlock()
}

// dispatch routes the request and returns the resolved operationId plus the
// 1-based index of the token that authenticated it (0 if none).
func (s *Server) dispatch(w *recorder, r *http.Request, body []byte) (string, int) {
	s.mu.Lock()
	over := len(s.log) >= s.opts.MaxRequests
	s.mu.Unlock()
	if over {
		writeError(w, http.StatusServiceUnavailable,
			fmt.Sprintf("harness backstop: more than %d requests in one run", s.opts.MaxRequests))
		return "", 0
	}

	rest, ok := strings.CutPrefix(r.URL.Path, BasePath)
	if !ok || (rest != "" && !strings.HasPrefix(rest, "/")) {
		writeError(w, http.StatusNotFound, "no such resource")
		return "", 0
	}

	switch {
	case rest == "/auth/token":
		switch r.Method {
		case http.MethodPost:
			return "create", s.handleCreate(w, body)
		case http.MethodDelete:
			return "delete", s.handleDelete(w, r)
		default:
			w.Header().Set("Allow", "POST, DELETE")
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return "", 0
		}

	case rest == "/groups/applications":
		if r.Method != http.MethodGet {
			w.Header().Set("Allow", "GET")
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return "", 0
		}
		return "listApplications", s.handleList(w, r)

	case strings.HasPrefix(rest, "/groups/applications/"):
		id := strings.TrimPrefix(rest, "/groups/applications/")
		if id == "" || strings.Contains(id, "/") {
			writeError(w, http.StatusNotFound, "no such resource")
			return "", 0
		}
		if r.Method != http.MethodGet {
			w.Header().Set("Allow", "GET")
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return "", 0
		}
		return "getApplicationById", s.handleGet(w, r, id)

	default:
		writeError(w, http.StatusNotFound, "no such resource")
		return "", 0
	}
}

// handleCreate implements operationId "create" (POST /auth/token).
func (s *Server) handleCreate(w *recorder, body []byte) int {
	if len(body) == 0 {
		writeError(w, http.StatusBadRequest, "request body is required")
		return 0
	}
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(body, &raw); err != nil {
		writeError(w, http.StatusBadRequest, "request body is not a JSON object")
		return 0
	}
	if err := allowKeys(raw, "username", "password", "domain"); err != nil {
		writeError(w, http.StatusBadRequest, "UserCredential: "+err.Error())
		return 0
	}
	if rawDomain, present := raw["domain"]; present {
		var domain map[string]json.RawMessage
		if err := json.Unmarshal(rawDomain, &domain); err != nil {
			writeError(w, http.StatusBadRequest, "Domain: not a JSON object")
			return 0
		}
		if err := allowKeys(domain, "domain_type", "value"); err != nil {
			writeError(w, http.StatusBadRequest, "Domain: "+err.Error())
			return 0
		}
		if rawType, present := domain["domain_type"]; present {
			var dt string
			if err := json.Unmarshal(rawType, &dt); err != nil {
				writeError(w, http.StatusBadRequest, "Domain.domain_type: not a string")
				return 0
			}
			if dt != "LOCAL" && dt != "LDAP" {
				writeError(w, http.StatusBadRequest, "Domain.domain_type: not in enum [LDAP, LOCAL]")
				return 0
			}
		}
	}

	var creds struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	_ = json.Unmarshal(body, &creds)
	if s.opts.RejectCredentials || creds.Username == "" || creds.Password == "" {
		writeError(w, http.StatusUnauthorized, "invalid credentials")
		return 0
	}

	s.mu.Lock()
	index := len(s.tokens) + 1
	ttl := 24 * time.Hour
	if s.opts.TokenTTL != nil {
		ttl = s.opts.TokenTTL(index)
	}
	budget := -1
	if s.opts.RequestBudget != nil {
		budget = s.opts.RequestBudget(index)
	}
	tok := &tokenState{
		value:  base64.StdEncoding.EncodeToString([]byte(fmt.Sprintf("ni-token-%04d", index))),
		expiry: s.opts.Now().Add(ttl),
		budget: budget,
	}
	s.tokens = append(s.tokens, tok)
	s.mu.Unlock()

	// Token.expiry is emitted in epoch milliseconds, matching the spec examples.
	writeJSON(w, http.StatusOK, map[string]any{
		"token":  tok.value,
		"expiry": tok.expiry.UnixMilli(),
	})
	return index
}

// handleDelete implements operationId "delete" (DELETE /auth/token).
func (s *Server) handleDelete(w *recorder, r *http.Request) int {
	index, ok := s.authenticate(r)
	if !ok {
		writeError(w, http.StatusUnauthorized, "token is invalid or expired")
		return index
	}
	s.mu.Lock()
	s.tokens[index-1].revoked = true
	s.mu.Unlock()
	w.WriteHeader(http.StatusNoContent)
	return index
}

// handleList implements operationId "listApplications" (GET /groups/applications).
func (s *Server) handleList(w *recorder, r *http.Request) int {
	index, ok := s.authenticate(r)
	if !ok {
		writeError(w, http.StatusUnauthorized, "token is invalid or expired")
		return index
	}
	q := r.URL.Query()
	if err := allowQuery(q, "size", "cursor", "modifiedAfter"); err != nil {
		writeError(w, http.StatusBadRequest, "listApplications: "+err.Error())
		return index
	}

	size := s.opts.DefaultPageSize
	if v := q.Get("size"); q.Has("size") {
		n, err := strconv.ParseFloat(v, 64)
		if err != nil || n <= 0 || n != float64(int(n)) {
			writeError(w, http.StatusBadRequest, "size: not a positive whole number")
			return index
		}
		size = int(n)
	}

	offset := 0
	if q.Has("cursor") {
		decoded, err := base64.StdEncoding.DecodeString(q.Get("cursor"))
		if err != nil {
			writeError(w, http.StatusBadRequest, "cursor: not a cursor issued by this server")
			return index
		}
		offset, err = strconv.Atoi(string(decoded))
		if err != nil || offset < 0 || offset > len(s.apps) {
			writeError(w, http.StatusBadRequest, "cursor: not a cursor issued by this server")
			return index
		}
	}

	if q.Has("modifiedAfter") {
		if _, err := strconv.ParseFloat(q.Get("modifiedAfter"), 64); err != nil {
			writeError(w, http.StatusBadRequest, "modifiedAfter: not a number")
			return index
		}
	}

	end := min(offset+size, len(s.apps))
	results := make([]map[string]any, 0, end-offset)
	for _, a := range s.apps[offset:end] {
		results = append(results, map[string]any{
			"entity_id":   a.EntityID,
			"entity_type": "Application",
			"entity_name": a.Name,
		})
	}

	resp := map[string]any{
		"results":     results,
		"total_count": len(s.apps),
		"start_time":  1597247025,
		"end_time":    1597247999,
	}
	if end < len(s.apps) {
		resp["cursor"] = base64.StdEncoding.EncodeToString([]byte(strconv.Itoa(end)))
	}
	writeJSON(w, http.StatusOK, resp)
	return index
}

// handleGet implements operationId "getApplicationById"
// (GET /groups/applications/{id}).
func (s *Server) handleGet(w *recorder, r *http.Request, id string) int {
	index, ok := s.authenticate(r)
	if !ok {
		writeError(w, http.StatusUnauthorized, "token is invalid or expired")
		return index
	}
	q := r.URL.Query()
	if err := allowQuery(q, "fetch_member_counts", "fetch_update_status"); err != nil {
		writeError(w, http.StatusBadRequest, "getApplicationById: "+err.Error())
		return index
	}
	wantCounts, err := boolParam(q, "fetch_member_counts")
	if err != nil {
		writeError(w, http.StatusBadRequest, "fetch_member_counts: "+err.Error())
		return index
	}
	wantStatus, err := boolParam(q, "fetch_update_status")
	if err != nil {
		writeError(w, http.StatusBadRequest, "fetch_update_status: "+err.Error())
		return index
	}

	var found *application
	for i := range s.apps {
		if s.apps[i].EntityID == id {
			found = &s.apps[i]
			break
		}
	}
	if found == nil {
		writeError(w, http.StatusNotFound, "no application with entity_id "+id)
		return index
	}

	resp := map[string]any{
		"entity_id":                found.EntityID,
		"name":                     found.Name,
		"entity_type":              "Application",
		"create_time":              1509410056733,
		"created_by":               "admin@local",
		"last_modified_time":       0,
		"last_modified_by":         "",
		"last_modified_by_service": "",
	}
	if wantCounts {
		resp["tier_count"] = found.TierCount
		resp["member_count"] = found.MemberCount
	}
	if wantStatus {
		resp["update_status"] = found.UpdateStatus
	}
	writeJSON(w, http.StatusOK, resp)
	return index
}

// authenticate resolves the Authorization header to a token index and consumes
// one unit of that token's request budget.
func (s *Server) authenticate(r *http.Request) (int, bool) {
	raw := r.Header.Get("Authorization")
	value, ok := strings.CutPrefix(raw, AuthPrefix)
	if !ok || value == "" {
		return 0, false
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	for i, tok := range s.tokens {
		if tok.value != value {
			continue
		}
		index := i + 1
		if tok.revoked {
			return index, false
		}
		if !s.opts.Now().Before(tok.expiry) {
			return index, false
		}
		if tok.budget == 0 {
			return index, false
		}
		if tok.budget > 0 {
			tok.budget--
		}
		return index, true
	}
	return 0, false
}

func allowKeys(obj map[string]json.RawMessage, allowed ...string) error {
	for k := range obj {
		if !contains(allowed, k) {
			return fmt.Errorf("property %q is not in the schema", k)
		}
	}
	return nil
}

func allowQuery(q url.Values, allowed ...string) error {
	keys := make([]string, 0, len(q))
	for k := range q {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		if !contains(allowed, k) {
			return fmt.Errorf("query parameter %q is not declared for this operation", k)
		}
		if len(q[k]) != 1 {
			return fmt.Errorf("query parameter %q repeated", k)
		}
	}
	return nil
}

func boolParam(q url.Values, name string) (bool, error) {
	if !q.Has(name) {
		return false, nil
	}
	switch q.Get(name) {
	case "true":
		return true, nil
	case "false":
		return false, nil
	default:
		return false, fmt.Errorf("not a boolean")
	}
}

func contains(haystack []string, needle string) bool {
	for _, h := range haystack {
		if h == needle {
			return true
		}
	}
	return false
}

func writeJSON(w *recorder, status int, payload any) {
	buf, err := json.Marshal(payload)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(buf)
}

func writeError(w *recorder, status int, message string) {
	writeJSON(w, status, map[string]any{"code": status, "message": message})
}

func readAll(r *http.Request) []byte {
	if r.Body == nil {
		return nil
	}
	defer func() { _ = r.Body.Close() }()
	buf := make([]byte, 0, 512)
	tmp := make([]byte, 512)
	for {
		n, err := r.Body.Read(tmp)
		buf = append(buf, tmp[:n]...)
		if err != nil {
			return buf
		}
	}
}
