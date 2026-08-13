// Package mock provides a loopback HTTP server that stands in for a VMware
// Cloud Foundation Operations appliance during tests.
//
// The server is pinned to docs/contract.json: it builds its routing table from
// the contract's operations and refuses every other method and path with 404,
// so a client that drifts off the contract fails loudly instead of silently
// exercising an endpoint the specification never described. Request bodies are
// checked against the contract's schemas for required and unknown properties.
//
// Every request the server sees, including refused ones, lands in a request log
// that tests read back with Requests. The server listens on 127.0.0.1 only and
// never contacts a live VMware endpoint.
//
// # Behaviour beyond the specification
//
// The OpenAPI document describes the wire shape of each operation, not the
// appliance's runtime conflict handling. Two behaviours are modelled here to
// make retry safety observable, and are not derived from the contract:
//
//   - createCustomGroup rejects a group whose resource key duplicates one that
//     already exists with 409 Conflict. A real appliance likewise refuses to
//     hold two custom groups under the same resource key.
//   - The fault injectors below simulate a lost response and a competing
//     writer. They are test scaffolding, not API surface.
package mock

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"

	"vcfops/internal/contract"
)

// DefaultAuthorization is the credential the server expects in the header the
// contract's security scheme names, unless WithAuthorization overrides it.
const DefaultAuthorization = "seed-operations-token"

// Request is one recorded inbound request.
type Request struct {
	// OperationID is the contract operation that matched, or "" when the
	// request fell outside the contract and was refused.
	OperationID string
	Method      string
	Path        string
	RawQuery    string
	Query       url.Values
	Header      http.Header
	Body        []byte
	Status      int
}

// DecodeBody unmarshals the recorded body into v.
func (r Request) DecodeBody(v any) error {
	if len(r.Body) == 0 {
		return fmt.Errorf("request to %s %s had an empty body", r.Method, r.Path)
	}
	return json.Unmarshal(r.Body, v)
}

// BodyMap decodes the recorded body as a generic JSON object, which is how the
// verifier inspects the exact set of keys that went over the wire.
func (r Request) BodyMap() (map[string]any, error) {
	var m map[string]any
	if err := r.DecodeBody(&m); err != nil {
		return nil, err
	}
	return m, nil
}

// Server is a loopback stand-in for the VCF Operations suite-api.
type Server struct {
	t        *testing.T
	contract *contract.Contract
	ts       *httptest.Server
	auth     string

	mu      sync.Mutex
	log     []Request
	groups  []map[string]any
	nextID  int
	failNew int
	inject  []map[string]any
}

// Option configures a Server.
type Option func(*Server)

// WithAuthorization sets the credential the server requires.
func WithAuthorization(v string) Option {
	return func(s *Server) { s.auth = v }
}

// New starts a loopback server pinned to docs/contract.json and registers its
// shutdown with t.
func New(t *testing.T, opts ...Option) *Server {
	t.Helper()
	c, err := contract.Load()
	if err != nil {
		t.Fatalf("mock: load contract: %v", err)
	}
	s := &Server{t: t, contract: c, auth: DefaultAuthorization}
	for _, o := range opts {
		o(s)
	}
	s.ts = httptest.NewServer(http.HandlerFunc(s.serve))
	t.Cleanup(s.ts.Close)
	return s
}

// URL is the base URL of the running server, for example http://127.0.0.1:PORT.
// Callers append the contract's base path themselves.
func (s *Server) URL() string { return s.ts.URL }

// Authorization is the credential the server requires.
func (s *Server) Authorization() string { return s.auth }

// Client returns an http.Client configured to reach this server.
func (s *Server) Client() *http.Client { return s.ts.Client() }

// Requests returns a copy of the request log in arrival order.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]Request(nil), s.log...)
}

// RequestsFor returns the recorded requests that matched a given operationId.
func (s *Server) RequestsFor(operationID string) []Request {
	var out []Request
	for _, r := range s.Requests() {
		if r.OperationID == operationID {
			out = append(out, r)
		}
	}
	return out
}

// CountFor returns how many recorded requests matched an operationId.
func (s *Server) CountFor(operationID string) int { return len(s.RequestsFor(operationID)) }

// Groups returns the custom groups currently held by the server, ordered by id.
func (s *Server) Groups() []map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]map[string]any, 0, len(s.groups))
	for _, g := range s.groups {
		out = append(out, cloneMap(g))
	}
	sort.Slice(out, func(i, j int) bool {
		return fmt.Sprint(out[i]["id"]) < fmt.Sprint(out[j]["id"])
	})
	return out
}

// GroupNames returns the resourceKey.name of every stored group, sorted.
func (s *Server) GroupNames() []string {
	var names []string
	for _, g := range s.Groups() {
		if rk, ok := g["resourceKey"].(map[string]any); ok {
			names = append(names, fmt.Sprint(rk["name"]))
		}
	}
	sort.Strings(names)
	return names
}

// Reset clears the request log and the stored groups.
func (s *Server) Reset() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log = nil
	s.groups = nil
	s.nextID = 0
	s.failNew = 0
	s.inject = nil
}

// FailNextCreateAfterStore makes the next createCustomGroup store the group and
// then fail with 503, modelling a response lost after the write committed. A
// retry-safe client must not create a second group when it tries again.
func (s *Server) FailNextCreateAfterStore() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.failNew++
}

// InsertBeforeNextCreate stores a group immediately before the next
// createCustomGroup is handled, modelling another writer that won the race
// after this client's lookup came back empty. The create then collides.
func (s *Server) InsertBeforeNextCreate(name, adapterKindKey, resourceKindKey string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.inject = append(s.inject, map[string]any{
		"resourceKey": map[string]any{
			"name":            name,
			"adapterKindKey":  adapterKindKey,
			"resourceKindKey": resourceKindKey,
		},
		"membershipDefinition": map[string]any{},
	})
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	rec := Request{
		Method:   r.Method,
		Path:     r.URL.Path,
		RawQuery: r.URL.RawQuery,
		Query:    r.URL.Query(),
		Header:   r.Header.Clone(),
		Body:     body,
	}

	op, known := s.contract.Route(r.Method, r.URL.Path)
	if !known {
		rec.Status = http.StatusNotFound
		s.record(rec)
		s.fail(w, http.StatusNotFound, fmt.Sprintf(
			"%s %s is not one of the operations named by docs/contract.json (%s)",
			r.Method, r.URL.Path, strings.Join(s.contract.SortedOperationIDs(), ", ")))
		return
	}
	rec.OperationID = op.OperationID

	if got := r.Header.Get(s.contract.Security.HeaderName); got != s.auth {
		rec.Status = http.StatusUnauthorized
		s.record(rec)
		s.fail(w, http.StatusUnauthorized, fmt.Sprintf(
			"missing or incorrect %s header", s.contract.Security.HeaderName))
		return
	}

	if err := s.checkParams(op, r.URL.Query()); err != nil {
		rec.Status = http.StatusBadRequest
		s.record(rec)
		s.fail(w, http.StatusBadRequest, err.Error())
		return
	}

	switch op.OperationID {
	case "getCustomGroups":
		s.getCustomGroups(w, r, &rec)
	case "createCustomGroup":
		s.createCustomGroup(w, r, &rec, body)
	default:
		rec.Status = http.StatusNotImplemented
		s.record(rec)
		s.fail(w, http.StatusNotImplemented,
			fmt.Sprintf("operation %s is named by the contract but not served", op.OperationID))
	}
}

// checkParams rejects query parameters the contract does not declare.
func (s *Server) checkParams(op contract.Operation, q url.Values) error {
	allowed := map[string]contract.Parameter{}
	for _, p := range op.Parameters {
		if p.In == "query" {
			allowed[p.Name] = p
		}
	}
	for name := range q {
		if _, ok := allowed[name]; !ok {
			return fmt.Errorf("query parameter %q is not declared by operation %s", name, op.OperationID)
		}
	}
	for name, p := range allowed {
		if p.Required && len(q[name]) == 0 {
			return fmt.Errorf("query parameter %q is required by operation %s", name, op.OperationID)
		}
	}
	if v := q.Get("includePolicy"); v != "" {
		if _, err := strconv.ParseBool(v); err != nil {
			return fmt.Errorf("includePolicy must be a boolean, got %q", v)
		}
	}
	return nil
}

func (s *Server) getCustomGroups(w http.ResponseWriter, r *http.Request, rec *Request) {
	q := r.URL.Query()
	wanted := map[string]bool{}
	for _, id := range q["groupId"] {
		wanted[id] = true
	}
	includePolicy, _ := strconv.ParseBool(q.Get("includePolicy"))

	s.mu.Lock()
	out := make([]map[string]any, 0, len(s.groups))
	for _, g := range s.groups {
		if len(wanted) > 0 && !wanted[fmt.Sprint(g["id"])] {
			continue
		}
		c := cloneMap(g)
		if !includePolicy {
			delete(c, "policy")
		}
		out = append(out, c)
	}
	s.mu.Unlock()

	sort.Slice(out, func(i, j int) bool {
		return fmt.Sprint(out[i]["id"]) < fmt.Sprint(out[j]["id"])
	})
	rec.Status = http.StatusOK
	s.record(*rec)
	s.write(w, http.StatusOK, map[string]any{"groups": out})
}

func (s *Server) createCustomGroup(w http.ResponseWriter, r *http.Request, rec *Request, body []byte) {
	if ct := r.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		rec.Status = http.StatusUnsupportedMediaType
		s.record(*rec)
		s.fail(w, http.StatusUnsupportedMediaType,
			fmt.Sprintf("createCustomGroup requires Content-Type application/json, got %q", ct))
		return
	}

	var decoded map[string]any
	if err := json.Unmarshal(body, &decoded); err != nil {
		rec.Status = http.StatusBadRequest
		s.record(*rec)
		s.fail(w, http.StatusBadRequest, fmt.Sprintf("request body is not a JSON object: %v", err))
		return
	}

	op, _ := s.contract.Operation("createCustomGroup")
	if op.RequestBody != nil {
		if err := s.contract.ValidateBody(op.RequestBody.Schema, decoded); err != nil {
			rec.Status = http.StatusBadRequest
			s.record(*rec)
			s.fail(w, http.StatusBadRequest, fmt.Sprintf("body does not match the contract: %v", err))
			return
		}
	}

	key, err := resourceKeyOf(decoded)
	if err != nil {
		rec.Status = http.StatusBadRequest
		s.record(*rec)
		s.fail(w, http.StatusBadRequest, err.Error())
		return
	}

	s.mu.Lock()
	for _, pending := range s.inject {
		pending["id"] = s.mintIDLocked()
		s.groups = append(s.groups, pending)
	}
	s.inject = nil

	for _, g := range s.groups {
		if k, err := resourceKeyOf(g); err == nil && k == key {
			s.mu.Unlock()
			rec.Status = http.StatusConflict
			s.record(*rec)
			s.fail(w, http.StatusConflict, fmt.Sprintf(
				"a custom group with resource key %s already exists", key))
			return
		}
	}

	stored := cloneMap(decoded)
	stored["id"] = s.mintIDLocked()
	s.groups = append(s.groups, stored)
	lost := s.failNew > 0
	if lost {
		s.failNew--
	}
	response := cloneMap(stored)
	s.mu.Unlock()

	if lost {
		rec.Status = http.StatusServiceUnavailable
		s.record(*rec)
		s.fail(w, http.StatusServiceUnavailable,
			"the group was created but the response was lost in transit")
		return
	}

	rec.Status = http.StatusCreated
	s.record(*rec)
	s.write(w, http.StatusCreated, response)
}

// mintIDLocked returns a deterministic RFC 4122 shaped identifier. The caller
// must hold s.mu.
func (s *Server) mintIDLocked() string {
	s.nextID++
	return fmt.Sprintf("00000000-0000-4000-8000-%012d", s.nextID)
}

// resourceKeyOf builds the composite identity the appliance treats as unique.
func resourceKeyOf(g map[string]any) (string, error) {
	rk, ok := g["resourceKey"].(map[string]any)
	if !ok {
		return "", fmt.Errorf("resourceKey is missing or not an object")
	}
	name, _ := rk["name"].(string)
	adapter, _ := rk["adapterKindKey"].(string)
	kind, _ := rk["resourceKindKey"].(string)
	if name == "" || adapter == "" || kind == "" {
		return "", fmt.Errorf(
			"resourceKey requires non-empty name, adapterKindKey and resourceKindKey")
	}
	return name + "\x00" + adapter + "\x00" + kind, nil
}

func (s *Server) record(r Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log = append(s.log, r)
}

func (s *Server) write(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func (s *Server) fail(w http.ResponseWriter, status int, message string) {
	s.write(w, status, map[string]any{
		"httpStatusCode": status,
		"message":        message,
	})
}

func cloneMap(m map[string]any) map[string]any {
	raw, err := json.Marshal(m)
	if err != nil {
		return map[string]any{}
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		return map[string]any{}
	}
	return out
}
