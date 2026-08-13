// Package opsmock is a loopback stand-in for a VMware Cloud Foundation
// Operations 9.1 appliance. It serves only the two operations named in
// docs/contract.json and records every request it receives so that a test can
// assert the exact wire shape a client produced.
//
// It listens on 127.0.0.1 only. No VMware endpoint is contacted.
package opsmock

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sort"
	"strconv"
	"strings"
	"sync"
)

// BasePath is contract.api.basePath.
const BasePath = "/suite-api"

// Contract-named operations. Anything else is not served.
const (
	PathAcquireToken       = BasePath + "/api/auth/token/acquire" // acquireToken
	PathSymptomDefinitions = BasePath + "/api/symptomdefinitions" // getSymptomDefinitions
)

// TokenPrefix is contract.clientRules.transport.authorizationHeader.valuePrefix.
const TokenPrefix = "vRealizeOpsToken "

// IssuedToken is the auth-token.token value this mock hands out.
const IssuedToken = "b1f7c0e2-9d34-4a51-8c6e-2f0a7d55e913::c0ffee"

// Credential values the mock accepts for acquireToken.
const (
	ValidUsername   = "svc-ops-reader"
	ValidPassword   = "Fx7-quiet-harbor-42"
	ValidAuthSource = "Imported LDAP Server"
)

// queryParams is the exact set of query parameter names getSymptomDefinitions
// declares in the specification. Any other name is rejected.
var queryParams = map[string]bool{
	"adapterKind":  true,
	"resourceKind": true,
	"id":           true,
	"name":         true,
	"page":         true,
	"pageSize":     true,
}

// bodyFields is the exact set of properties username-password declares.
var bodyFields = map[string]bool{"username": true, "password": true, "authSource": true}

// SymptomDefinition mirrors the subset of #/components/schemas/symptom-definition
// the fixture serves.
type SymptomDefinition struct {
	ID              string `json:"id"`
	Name            string `json:"name"`
	AdapterKindKey  string `json:"adapterKindKey"`
	ResourceKindKey string `json:"resourceKindKey"`
}

// Request is one recorded inbound request.
type Request struct {
	Seq         int
	Method      string
	Path        string
	RawQuery    string
	Query       map[string][]string
	QueryKeys   []string // sorted, deduplicated
	Accept      string
	ContentType string
	// AuthorizationPresent distinguishes an absent header from an empty one.
	AuthorizationPresent bool
	Authorization        string
	Body                 []byte
	// BodyKeys is the sorted set of top-level JSON object keys in Body, or nil
	// if Body is empty or is not a JSON object.
	BodyKeys []string
	Status   int
	// Rejection is the mock's reason for a 4xx, empty on success.
	Rejection string
}

// Server is a loopback VCF Operations mock.
type Server struct {
	http *httptest.Server

	mu   sync.Mutex
	log  []Request
	data []SymptomDefinition
}

// New starts a mock serving the given symptom definitions. The caller owns
// Close. The definitions are served in the order given; callers that want to
// prove a client sorts should pass a non-monotonic order.
func New(defs []SymptomDefinition) *Server {
	s := &Server{data: append([]SymptomDefinition(nil), defs...)}
	mux := http.NewServeMux()
	mux.HandleFunc(PathAcquireToken, s.handleAcquireToken)
	mux.HandleFunc(PathSymptomDefinitions, s.handleSymptomDefinitions)
	mux.HandleFunc("/", s.handleUnserved)
	s.http = httptest.NewServer(mux)
	return s
}

// URL is the base URL of the appliance, including the contract base path.
// A client should treat it as the value it would otherwise build from the
// appliance FQDN.
func (s *Server) URL() string { return s.http.URL + BasePath }

// Client returns an HTTP client configured to reach this mock.
func (s *Server) Client() *http.Client { return s.http.Client() }

// Close shuts the mock down.
func (s *Server) Close() { s.http.Close() }

// Requests returns a copy of the request log in arrival order.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.log))
	copy(out, s.log)
	return out
}

// Reset clears the request log.
func (s *Server) Reset() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log = nil
}

// record appends rec to the log and returns its sequence number.
func (s *Server) record(rec Request) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	rec.Seq = len(s.log)
	s.log = append(s.log, rec)
	return rec.Seq
}

// finish updates the recorded status and rejection reason for seq.
func (s *Server) finish(seq, status int, rejection string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log[seq].Status = status
	s.log[seq].Rejection = rejection
}

func capture(r *http.Request, body []byte) Request {
	auth, authOK := r.Header["Authorization"]
	rec := Request{
		Method:      r.Method,
		Path:        r.URL.Path,
		RawQuery:    r.URL.RawQuery,
		Query:       map[string][]string(r.URL.Query()),
		Accept:      r.Header.Get("Accept"),
		ContentType: r.Header.Get("Content-Type"),
		Body:        body,
	}
	if authOK {
		rec.AuthorizationPresent = true
		rec.Authorization = auth[0]
	}
	for k := range rec.Query {
		rec.QueryKeys = append(rec.QueryKeys, k)
	}
	sort.Strings(rec.QueryKeys)
	var obj map[string]json.RawMessage
	if len(body) > 0 && json.Unmarshal(body, &obj) == nil {
		for k := range obj {
			rec.BodyKeys = append(rec.BodyKeys, k)
		}
		sort.Strings(rec.BodyKeys)
	}
	return rec
}

// fail writes a JSON error and records the rejection reason.
func (s *Server) fail(w http.ResponseWriter, seq, status int, reason string) {
	s.finish(seq, status, reason)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"message": reason})
}

func acceptsJSON(accept string) bool {
	if accept == "" {
		return false
	}
	for _, part := range strings.Split(accept, ",") {
		media := strings.TrimSpace(strings.SplitN(part, ";", 2)[0])
		if media == "application/json" || media == "application/*" || media == "*/*" {
			return true
		}
	}
	return false
}

// handleUnserved answers every route the contract does not name.
func (s *Server) handleUnserved(w http.ResponseWriter, r *http.Request) {
	body := readBody(r)
	seq := s.record(capture(r, body))
	s.fail(w, seq, http.StatusNotFound,
		fmt.Sprintf("no contract operation is served at %s %s", r.Method, r.URL.Path))
}

func readBody(r *http.Request) []byte {
	if r.Body == nil {
		return nil
	}
	buf := make([]byte, 0, 512)
	tmp := make([]byte, 512)
	for {
		n, err := r.Body.Read(tmp)
		buf = append(buf, tmp[:n]...)
		if err != nil {
			break
		}
	}
	return buf
}

// handleAcquireToken serves operationId acquireToken.
func (s *Server) handleAcquireToken(w http.ResponseWriter, r *http.Request) {
	body := readBody(r)
	seq := s.record(capture(r, body))

	if r.Method != http.MethodPost {
		s.fail(w, seq, http.StatusMethodNotAllowed, "acquireToken is POST")
		return
	}
	if r.URL.RawQuery != "" {
		s.fail(w, seq, http.StatusBadRequest, "acquireToken declares no query parameters")
		return
	}
	// acquireToken declares an empty security array: it must be unauthenticated.
	if _, ok := r.Header["Authorization"]; ok {
		s.fail(w, seq, http.StatusBadRequest,
			"acquireToken declares security: [] and must not carry an Authorization header")
		return
	}
	if ct := strings.TrimSpace(strings.SplitN(r.Header.Get("Content-Type"), ";", 2)[0]); ct != "application/json" {
		s.fail(w, seq, http.StatusUnsupportedMediaType,
			fmt.Sprintf("Content-Type must be application/json, got %q", r.Header.Get("Content-Type")))
		return
	}
	if !acceptsJSON(r.Header.Get("Accept")) {
		s.fail(w, seq, http.StatusNotAcceptable,
			fmt.Sprintf("Accept must allow application/json, got %q", r.Header.Get("Accept")))
		return
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal(body, &raw); err != nil {
		s.fail(w, seq, http.StatusBadRequest, "body is not a JSON object")
		return
	}
	for k := range raw {
		if !bodyFields[k] {
			s.fail(w, seq, http.StatusBadRequest,
				fmt.Sprintf("username-password declares no property %q", k))
			return
		}
	}
	for _, k := range []string{"username", "password"} {
		if _, ok := raw[k]; !ok {
			s.fail(w, seq, http.StatusBadRequest, fmt.Sprintf("%q is required", k))
			return
		}
	}
	// An optional property that the caller left unset must be absent, not sent
	// as null or as an empty string.
	if v, ok := raw["authSource"]; ok {
		var s2 *string
		if err := json.Unmarshal(v, &s2); err != nil {
			s.fail(w, seq, http.StatusBadRequest, "authSource must be a string")
			return
		}
		if s2 == nil {
			s.fail(w, seq, http.StatusBadRequest,
				"authSource is optional: omit the property instead of sending null")
			return
		}
		if *s2 == "" {
			s.fail(w, seq, http.StatusBadRequest,
				"authSource is optional: omit the property instead of sending an empty string")
			return
		}
		if *s2 != ValidAuthSource {
			s.fail(w, seq, http.StatusUnauthorized, "unknown auth source")
			return
		}
	}

	var creds struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	_ = json.Unmarshal(body, &creds)
	if creds.Username != ValidUsername || creds.Password != ValidPassword {
		s.fail(w, seq, http.StatusUnauthorized, "authentication failed")
		return
	}

	s.finish(seq, http.StatusOK, "")
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"token":     IssuedToken,
		"validity":  int64(1778668800000),
		"expiresAt": "Wednesday, May 13, 2026 08:00:00 AM UTC",
		"roles":     []string{"ReadOnly"},
	})
}

// handleSymptomDefinitions serves operationId getSymptomDefinitions.
func (s *Server) handleSymptomDefinitions(w http.ResponseWriter, r *http.Request) {
	body := readBody(r)
	seq := s.record(capture(r, body))

	if r.Method != http.MethodGet {
		s.fail(w, seq, http.StatusMethodNotAllowed, "getSymptomDefinitions is GET")
		return
	}
	auth, ok := r.Header["Authorization"]
	if !ok {
		s.fail(w, seq, http.StatusUnauthorized, "missing Authorization header")
		return
	}
	if auth[0] != TokenPrefix+IssuedToken {
		s.fail(w, seq, http.StatusUnauthorized,
			fmt.Sprintf("Authorization must be %q followed by the acquired token, got %q", TokenPrefix, auth[0]))
		return
	}
	if !acceptsJSON(r.Header.Get("Accept")) {
		s.fail(w, seq, http.StatusNotAcceptable,
			fmt.Sprintf("Accept must allow application/json, got %q", r.Header.Get("Accept")))
		return
	}

	q := r.URL.Query()
	for k, vals := range q {
		if !queryParams[k] {
			s.fail(w, seq, http.StatusBadRequest,
				fmt.Sprintf("getSymptomDefinitions declares no query parameter %q", k))
			return
		}
		// An optional parameter the caller left unset must be absent from the
		// query string, not present with an empty value.
		for _, v := range vals {
			if v == "" {
				s.fail(w, seq, http.StatusBadRequest,
					fmt.Sprintf("query parameter %q is optional: omit it instead of sending an empty value", k))
				return
			}
		}
		if k != "id" && len(vals) > 1 {
			s.fail(w, seq, http.StatusBadRequest,
				fmt.Sprintf("query parameter %q is not an array and must appear once", k))
			return
		}
	}
	page, err := intParam(q, "page", 0)
	if err != nil || page < 0 {
		s.fail(w, seq, http.StatusBadRequest, "page must be a non-negative integer")
		return
	}
	pageSize, err := intParam(q, "pageSize", 1000)
	if err != nil || pageSize < 1 {
		s.fail(w, seq, http.StatusBadRequest, "pageSize must be a positive integer")
		return
	}

	matched := s.filter(q)
	total := len(matched)
	lo := page * pageSize
	if lo > total {
		lo = total
	}
	hi := lo + pageSize
	if hi > total {
		hi = total
	}
	entries := matched[lo:hi]

	links := []map[string]string{{
		"href": fmt.Sprintf("%s?page=%d&pageSize=%d", PathSymptomDefinitions, page, pageSize),
		"rel":  "SELF",
	}}
	if hi < total {
		links = append(links, map[string]string{
			"href": fmt.Sprintf("%s?page=%d&pageSize=%d", PathSymptomDefinitions, page+1, pageSize),
			"rel":  "NEXT",
		})
	}

	s.finish(seq, http.StatusOK, "")
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"links": links,
		"pageInfo": map[string]any{
			"page":       page,
			"pageSize":   pageSize,
			"totalCount": total,
		},
		"symptomDefinitions": entries,
	})
}

func intParam(q map[string][]string, name string, def int) (int, error) {
	vals, ok := q[name]
	if !ok {
		return def, nil
	}
	return strconv.Atoi(vals[0])
}

// filter applies the spec's search parameters to the fixture data, preserving
// the fixture's own (deliberately non-monotonic) order.
func (s *Server) filter(q map[string][]string) []SymptomDefinition {
	ids := map[string]bool{}
	for _, v := range q["id"] {
		ids[v] = true
	}
	adapterKind := first(q, "adapterKind")
	resourceKind := first(q, "resourceKind")
	name := strings.ToLower(first(q, "name"))

	var out []SymptomDefinition
	for _, d := range s.data {
		if len(ids) > 0 && !ids[d.ID] {
			continue
		}
		if adapterKind != "" && d.AdapterKindKey != adapterKind {
			continue
		}
		if resourceKind != "" && d.ResourceKindKey != resourceKind {
			continue
		}
		if name != "" && !strings.Contains(strings.ToLower(d.Name), name) {
			continue
		}
		out = append(out, d)
	}
	return out
}

func first(q map[string][]string, name string) string {
	if v, ok := q[name]; ok && len(v) > 0 {
		return v[0]
	}
	return ""
}
