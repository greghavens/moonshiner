// Package mock runs a loopback HTTP stand-in for a VCF Operations appliance.
//
// The server is pinned to a contract document (docs/contract.json): it serves
// only the operations that contract names, at exactly the method and path the
// contract declares, and it rejects request bodies whose top-level properties
// are not in the contracted schema. Anything else answers 404.
//
// Every exchange, including rejected ones, is appended to a request log that
// tests can read back with Requests.
//
// This package is fixed infrastructure. Do not edit it; write tests against it.
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

	"vcfops.local/opssync/opsapi"
)

// AuthScheme is the credential scheme the appliance expects in the
// Authorization header, as in "Authorization: OpsToken <token>".
const AuthScheme = "OpsToken"

// ClockMillis is the mock's frozen wall clock, in milliseconds since the Unix
// epoch, so that issued tokens are byte-for-byte reproducible.
const ClockMillis int64 = 1767225600000 // 2026-01-01T00:00:00Z

// TokenValidityMillis is how long an issued token claims to be valid for.
const TokenValidityMillis int64 = 6 * 60 * 60 * 1000

// DefaultPageSize is the getResources page size the specification defaults to
// when the caller sends no pageSize parameter.
const DefaultPageSize = 1000

// Script parameterises one mock run.
type Script struct {
	// Resources is the inventory getResources pages over, in order.
	Resources []opsapi.Resource

	// ExpiresAfter[i] is how many authenticated requests the i-th issued token
	// serves before it starts answering 401. A zero entry, or an index past the
	// end of the slice, means that token never expires. Token acquisition is
	// unauthenticated and never counts against the budget.
	ExpiresAfter []int
}

// Request is one logged exchange.
type Request struct {
	// OperationID is the contracted operation that matched, or "" if none did.
	OperationID string
	Method      string
	Path        string
	Query       url.Values
	Header      http.Header
	Body        []byte
	// Token is the credential extracted from the Authorization header, or ""
	// when the header was absent or did not use the expected scheme.
	Token string
	// Status is the response status the mock returned.
	Status int
}

// DecodeBody unmarshals the logged request body into a generic JSON object.
func (r Request) DecodeBody() (map[string]any, error) {
	var m map[string]any
	if err := json.Unmarshal(r.Body, &m); err != nil {
		return nil, fmt.Errorf("decode body of %s %s: %w", r.Method, r.Path, err)
	}
	return m, nil
}

// AcceptedBatch records one property batch the mock accepted.
type AcceptedBatch struct {
	// RequestIndex is the index into Requests of the exchange that carried it.
	RequestIndex int
	// ResourceIDs are the resourceIds the batch carried, in wire order.
	ResourceIDs []string
	// StatKeys maps each resourceId to the statKeys carried for it, in wire order.
	StatKeys map[string][]string
}

// Server is a running loopback mock.
type Server struct {
	t        testing.TB
	contract *Contract
	script   Script
	http     *httptest.Server

	mu       sync.Mutex
	requests []Request
	tokens   []*tokenState
	accepted []AcceptedBatch
}

type tokenState struct {
	value  string
	served int
	limit  int
}

// handlers is the set of operationIds this mock knows how to serve. A contract
// that names anything else is a contract error.
var handlers = map[string]func(*Server, http.ResponseWriter, *http.Request, *Request, Operation){
	"acquireToken":           (*Server).handleAcquireToken,
	"getResources":           (*Server).handleGetResources,
	"addResourcesProperties": (*Server).handleAddResourcesProperties,
}

// Start loads the contract at contractPath and starts a loopback server pinned
// to it. The server is shut down when the test finishes.
func Start(t testing.TB, contractPath string, script Script) *Server {
	t.Helper()

	contract, err := LoadContract(contractPath)
	if err != nil {
		t.Fatalf("mock: %v", err)
	}

	supported := make([]string, 0, len(handlers))
	for id := range handlers {
		supported = append(supported, id)
	}
	sort.Strings(supported)
	for _, op := range contract.Operations {
		if _, ok := handlers[op.OperationID]; !ok {
			t.Fatalf("mock: contract names operationId %q, which this mock cannot serve; supported: %s",
				op.OperationID, strings.Join(supported, ", "))
		}
	}

	s := &Server{t: t, contract: contract, script: script}
	s.http = httptest.NewServer(http.HandlerFunc(s.serve))
	t.Cleanup(s.http.Close)
	return s
}

// URL is the scheme://host:port the mock listens on.
func (s *Server) URL() string { return s.http.URL }

// Contract is the contract the mock was pinned to.
func (s *Server) Contract() *Contract { return s.contract }

// Requests returns a copy of the request log, in the order the mock handled
// the exchanges.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]Request(nil), s.requests...)
}

// RequestsFor returns the logged exchanges that matched one operationId.
func (s *Server) RequestsFor(operationID string) []Request {
	var out []Request
	for _, r := range s.Requests() {
		if r.OperationID == operationID {
			out = append(out, r)
		}
	}
	return out
}

// IssuedTokens returns every token the mock handed out, in issue order.
func (s *Server) IssuedTokens() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]string, 0, len(s.tokens))
	for _, tok := range s.tokens {
		out = append(out, tok.value)
	}
	return out
}

// AcceptedBatches returns every property batch the mock accepted.
func (s *Server) AcceptedBatches() []AcceptedBatch {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]AcceptedBatch(nil), s.accepted...)
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	_ = r.Body.Close()

	entry := Request{
		Method: r.Method,
		Path:   r.URL.Path,
		Query:  r.URL.Query(),
		Header: r.Header.Clone(),
		Body:   body,
		Token:  extractToken(r.Header.Get("Authorization")),
	}

	op, matched, pathKnown := s.match(r)
	if !matched {
		if pathKnown {
			s.finish(w, &entry, http.StatusMethodNotAllowed, nil)
			return
		}
		s.finish(w, &entry, http.StatusNotFound, nil)
		return
	}
	entry.OperationID = op.OperationID

	if !acceptsJSON(r.Header.Get("Accept")) {
		s.finish(w, &entry, http.StatusNotAcceptable, nil)
		return
	}
	if len(body) > 0 && !isJSONContentType(r.Header.Get("Content-Type")) {
		s.finish(w, &entry, http.StatusUnsupportedMediaType, nil)
		return
	}
	if bad := unknownQueryParam(op, r.URL.Query()); bad != "" {
		s.finish(w, &entry, http.StatusBadRequest, nil)
		return
	}
	if op.Authenticated && !s.consumeToken(entry.Token) {
		s.finish(w, &entry, http.StatusUnauthorized, nil)
		return
	}
	if op.RequestSchema != "" {
		if !s.bodyMatchesSchema(op.RequestSchema, body) {
			s.finish(w, &entry, http.StatusBadRequest, nil)
			return
		}
	}

	handlers[op.OperationID](s, w, r, &entry, op)
}

// match finds the contracted operation for a request. It reports whether an
// operation matched and, when it did not, whether some operation at least
// claims the request path.
func (s *Server) match(r *http.Request) (Operation, bool, bool) {
	pathKnown := false
	for _, op := range s.contract.Operations {
		full := s.contract.BasePath + op.Path
		if full != r.URL.Path {
			continue
		}
		pathKnown = true
		if op.Method == r.Method {
			return op, true, true
		}
	}
	return Operation{}, false, pathKnown
}

func (s *Server) finish(w http.ResponseWriter, entry *Request, status int, payload any) {
	entry.Status = status

	s.mu.Lock()
	s.requests = append(s.requests, *entry)
	s.mu.Unlock()

	if payload == nil {
		w.WriteHeader(status)
		return
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		s.t.Errorf("mock: encode response for %s %s: %v", entry.Method, entry.Path, err)
		w.WriteHeader(http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(encoded)
}

func (s *Server) consumeToken(value string) bool {
	if value == "" {
		return false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, tok := range s.tokens {
		if tok.value != value {
			continue
		}
		if tok.limit > 0 && tok.served >= tok.limit {
			return false
		}
		tok.served++
		return true
	}
	return false
}

func (s *Server) bodyMatchesSchema(name string, body []byte) bool {
	schema, ok := s.contract.Schemas[name]
	if !ok {
		return false
	}
	var decoded map[string]any
	if err := json.Unmarshal(body, &decoded); err != nil {
		return false
	}
	for _, required := range schema.Required {
		if _, present := decoded[required]; !present {
			return false
		}
	}
	for property := range decoded {
		if !schema.Has(property) {
			return false
		}
	}
	return true
}

func (s *Server) handleAcquireToken(w http.ResponseWriter, r *http.Request, entry *Request, op Operation) {
	s.mu.Lock()
	index := len(s.tokens)
	limit := 0
	if index < len(s.script.ExpiresAfter) {
		limit = s.script.ExpiresAfter[index]
	}
	tok := &tokenState{value: fmt.Sprintf("ops-token-%d", index+1), limit: limit}
	s.tokens = append(s.tokens, tok)
	s.mu.Unlock()

	s.finish(w, entry, op.SuccessStatus, map[string]any{
		"token":     tok.value,
		"validity":  ClockMillis + TokenValidityMillis,
		"expiresAt": "Thursday, January 1, 2026 06:00:00 AM UTC",
		"roles":     []string{"ContentAdmin"},
	})
}

func (s *Server) handleGetResources(w http.ResponseWriter, r *http.Request, entry *Request, op Operation) {
	query := r.URL.Query()

	page, ok := intParam(query, "page", 0)
	if !ok || page < 0 {
		s.finish(w, entry, http.StatusBadRequest, nil)
		return
	}
	pageSize, ok := intParam(query, "pageSize", DefaultPageSize)
	if !ok || pageSize < 1 {
		s.finish(w, entry, http.StatusBadRequest, nil)
		return
	}

	selected := filterResources(s.script.Resources, query)

	start := page * pageSize
	if start > len(selected) {
		start = len(selected)
	}
	end := start + pageSize
	if end > len(selected) {
		end = len(selected)
	}

	list := make([]map[string]any, 0, end-start)
	for _, res := range selected[start:end] {
		list = append(list, map[string]any{
			"identifier": res.Identifier,
			"resourceKey": map[string]any{
				"name":            res.Name,
				"adapterKindKey":  res.AdapterKindKey,
				"resourceKindKey": res.ResourceKindKey,
			},
			"creationTime":        ClockMillis,
			"resourceHealth":      "GREEN",
			"resourceHealthValue": 100.0,
			"resourceStatusStates": []map[string]any{{
				"resourceState":  "STARTED",
				"resourceStatus": "DATA_RECEIVING",
			}},
		})
	}

	links := []map[string]any{{
		"href": fmt.Sprintf("%s%s?page=%d&pageSize=%d", s.contract.BasePath, op.Path, page, pageSize),
		"rel":  "SELF",
	}}
	if end < len(selected) {
		links = append(links, map[string]any{
			"href": fmt.Sprintf("%s%s?page=%d&pageSize=%d", s.contract.BasePath, op.Path, page+1, pageSize),
			"rel":  "NEXT",
		})
	}

	s.finish(w, entry, op.SuccessStatus, map[string]any{
		"pageInfo": map[string]any{
			"page":       page,
			"pageSize":   pageSize,
			"totalCount": len(selected),
		},
		"links":        links,
		"resourceList": list,
	})
}

func (s *Server) handleAddResourcesProperties(w http.ResponseWriter, r *http.Request, entry *Request, op Operation) {
	var payload struct {
		Values []struct {
			ResourceID       string `json:"resourceId"`
			PropertyContents struct {
				PropertyContent []struct {
					StatKey    string    `json:"statKey"`
					Timestamps []int64   `json:"timestamps"`
					Values     []string  `json:"values"`
					Data       []float64 `json:"data"`
				} `json:"property-content"`
			} `json:"property-contents"`
		} `json:"values"`
	}
	if err := json.Unmarshal(entry.Body, &payload); err != nil {
		s.finish(w, entry, http.StatusBadRequest, nil)
		return
	}
	if len(payload.Values) == 0 {
		s.finish(w, entry, http.StatusBadRequest, nil)
		return
	}

	batch := AcceptedBatch{StatKeys: map[string][]string{}}
	for _, value := range payload.Values {
		if value.ResourceID == "" || len(value.PropertyContents.PropertyContent) == 0 {
			s.finish(w, entry, http.StatusBadRequest, nil)
			return
		}
		batch.ResourceIDs = append(batch.ResourceIDs, value.ResourceID)
		for _, content := range value.PropertyContents.PropertyContent {
			if content.StatKey == "" || len(content.Timestamps) == 0 {
				s.finish(w, entry, http.StatusBadRequest, nil)
				return
			}
			if len(content.Values) > 0 && len(content.Data) > 0 {
				s.finish(w, entry, http.StatusBadRequest, nil)
				return
			}
			if len(content.Values) == 0 && len(content.Data) == 0 {
				s.finish(w, entry, http.StatusBadRequest, nil)
				return
			}
			batch.StatKeys[value.ResourceID] = append(batch.StatKeys[value.ResourceID], content.StatKey)
		}
	}

	entry.Status = op.SuccessStatus
	s.mu.Lock()
	s.requests = append(s.requests, *entry)
	batch.RequestIndex = len(s.requests) - 1
	s.accepted = append(s.accepted, batch)
	s.mu.Unlock()

	w.WriteHeader(op.SuccessStatus)
}

func filterResources(all []opsapi.Resource, query url.Values) []opsapi.Resource {
	out := make([]opsapi.Resource, 0, len(all))
	for _, res := range all {
		if !matchesAny(query["name"], res.Name) {
			continue
		}
		if !matchesAny(query["adapterKind"], res.AdapterKindKey) {
			continue
		}
		if !matchesAny(query["resourceKind"], res.ResourceKindKey) {
			continue
		}
		out = append(out, res)
	}
	return out
}

func matchesAny(wanted []string, actual string) bool {
	if len(wanted) == 0 {
		return true
	}
	for _, w := range wanted {
		if w == actual {
			return true
		}
	}
	return false
}

func unknownQueryParam(op Operation, query url.Values) string {
	allowed := map[string]bool{}
	for _, name := range op.QueryParams {
		allowed[name] = true
	}
	names := make([]string, 0, len(query))
	for name := range query {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		if !allowed[name] {
			return name
		}
	}
	return ""
}

func intParam(query url.Values, name string, fallback int) (int, bool) {
	raw, present := query[name]
	if !present {
		return fallback, true
	}
	if len(raw) != 1 {
		return 0, false
	}
	parsed, err := strconv.Atoi(raw[0])
	if err != nil {
		return 0, false
	}
	return parsed, true
}

func extractToken(header string) string {
	prefix := AuthScheme + " "
	if !strings.HasPrefix(header, prefix) {
		return ""
	}
	return strings.TrimSpace(strings.TrimPrefix(header, prefix))
}

func acceptsJSON(header string) bool {
	if header == "" {
		return false
	}
	for _, part := range strings.Split(header, ",") {
		media := strings.TrimSpace(strings.SplitN(part, ";", 2)[0])
		if media == "application/json" || media == "*/*" || media == "application/*" {
			return true
		}
	}
	return false
}

func isJSONContentType(header string) bool {
	media := strings.TrimSpace(strings.SplitN(header, ";", 2)[0])
	return strings.EqualFold(media, "application/json")
}
