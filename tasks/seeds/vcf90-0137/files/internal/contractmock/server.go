// Package contractmock provides the protected loopback server for the focused
// VCF Operations for Networks contract in docs/contract.json.
//
// It serves exactly operationIds create and listTroubleshootingIncidents. Every
// request is recorded and exposed through Log for the verifier.
package contractmock

import (
	"encoding/base64"
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
)

const (
	BasePath   = "/api/ni"
	AuthPrefix = "NetworkInsight "
)

// Route describes one operation implemented by the mock.
type Route struct {
	OperationID string
	Method      string
	Path        string
}

// ContractOperations is the complete mock route table.
func ContractOperations() []Route {
	return []Route{
		{OperationID: "create", Method: http.MethodPost, Path: "/auth/token"},
		{OperationID: "listTroubleshootingIncidents", Method: http.MethodGet, Path: "/gnt/troubleshoot/incidents"},
	}
}

// Entry is one request observed by the mock.
type Entry struct {
	Seq           int
	OperationID   string
	Method        string
	Path          string
	Query         url.Values
	Authorization string
	ContentType   string
	Body          []byte
	Status        int
	TokenIndex    int
}

// QueryKeys returns the sorted set of query names present on the wire.
func (e Entry) QueryKeys() []string {
	keys := make([]string, 0, len(e.Query))
	for key := range e.Query {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

// Options configure a mock scenario.
type Options struct {
	// Incidents is the number of fixture incidents. Zero uses 23.
	Incidents int
	// ExpireFirstToken makes the first token return 401 after it has served one
	// successful list page. This deterministically models mid-run expiry.
	ExpireFirstToken bool
}

type tokenState struct {
	value             string
	successfulListUse int
}

type incident struct {
	EntityID      string `json:"entity_id"`
	StartEntityID string `json:"start_entity_id"`
	Name          string `json:"name"`
	Status        string `json:"status"`
}

// Server is a running in-process HTTP server bound by httptest to loopback.
type Server struct {
	opts Options
	http *httptest.Server
	data []incident

	mu     sync.Mutex
	log    []Entry
	tokens []*tokenState
}

// New starts a mock. Call Close when the scenario completes.
func New(opts Options) *Server {
	if opts.Incidents == 0 {
		opts.Incidents = 23
	}
	s := &Server{opts: opts, data: buildIncidents(opts.Incidents)}
	s.http = httptest.NewServer(http.HandlerFunc(s.serveHTTP))
	return s
}

func buildIncidents(count int) []incident {
	statuses := []string{"OPEN", "IN_PROGRESS", "RESOLVED"}
	result := make([]incident, count)
	for i := range result {
		result[i] = incident{
			EntityID:      fmt.Sprintf("18230:999:%06d", i+1),
			StartEntityID: fmt.Sprintf("18230:1:%06d", 100+i),
			Name:          fmt.Sprintf("incident-%02d", i+1),
			Status:        statuses[i%len(statuses)],
		}
	}
	return result
}

// URL is the loopback origin. The client must append BasePath itself.
func (s *Server) URL() string { return s.http.URL }

// Close stops the server.
func (s *Server) Close() { s.http.Close() }

// Log returns a deep-copy snapshot of the request log.
func (s *Server) Log() []Entry {
	s.mu.Lock()
	defer s.mu.Unlock()
	result := make([]Entry, len(s.log))
	for i, entry := range s.log {
		result[i] = entry
		result[i].Body = append([]byte(nil), entry.Body...)
		result[i].Query = cloneValues(entry.Query)
	}
	return result
}

// TokensIssued reports the number of successful create responses.
func (s *Server) TokensIssued() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.tokens)
}

// TokenValue returns the value of the one-based token index.
func (s *Server) TokenValue(index int) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if index < 1 || index > len(s.tokens) {
		return ""
	}
	return s.tokens[index-1].value
}

// IncidentIDs returns fixture entity IDs in response order.
func (s *Server) IncidentIDs() []string {
	result := make([]string, len(s.data))
	for i := range s.data {
		result[i] = s.data[i].EntityID
	}
	return result
}

func cloneValues(values url.Values) url.Values {
	result := make(url.Values, len(values))
	for key, source := range values {
		result[key] = append([]string(nil), source...)
	}
	return result
}

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	entry := Entry{
		Method:        r.Method,
		Path:          r.URL.Path,
		Query:         cloneValues(r.URL.Query()),
		Authorization: r.Header.Get("Authorization"),
		ContentType:   r.Header.Get("Content-Type"),
		Body:          body,
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	entry.Seq = len(s.log) + 1
	for i, token := range s.tokens {
		if entry.Authorization == AuthPrefix+token.value {
			entry.TokenIndex = i + 1
			break
		}
	}

	switch r.URL.Path {
	case BasePath + "/auth/token":
		if r.Method != http.MethodPost {
			s.respond(w, &entry, http.StatusMethodNotAllowed, nil)
			return
		}
		entry.OperationID = "create"
		s.handleCreate(w, &entry)
	case BasePath + "/gnt/troubleshoot/incidents":
		if r.Method != http.MethodGet {
			s.respond(w, &entry, http.StatusMethodNotAllowed, nil)
			return
		}
		entry.OperationID = "listTroubleshootingIncidents"
		s.handleList(w, &entry)
	default:
		s.respond(w, &entry, http.StatusNotFound, nil)
	}
}

func (s *Server) handleCreate(w http.ResponseWriter, entry *Entry) {
	index := len(s.tokens) + 1
	token := &tokenState{value: fmt.Sprintf("vcfon-token-%d", index)}
	s.tokens = append(s.tokens, token)
	payload := map[string]any{
		"token":  token.value,
		"expiry": int64(4102444800000),
	}
	s.respond(w, entry, http.StatusOK, payload)
}

func (s *Server) handleList(w http.ResponseWriter, entry *Entry) {
	if entry.TokenIndex == 0 {
		s.respond(w, entry, http.StatusUnauthorized, nil)
		return
	}
	token := s.tokens[entry.TokenIndex-1]
	if s.opts.ExpireFirstToken && entry.TokenIndex == 1 && token.successfulListUse >= 1 {
		s.respond(w, entry, http.StatusUnauthorized, nil)
		return
	}

	pageSize := 10
	if entry.Query.Has("size") {
		value, err := strconv.Atoi(entry.Query.Get("size"))
		if err != nil || value <= 0 {
			s.respond(w, entry, http.StatusBadRequest, nil)
			return
		}
		pageSize = value
	}

	offset := 0
	if entry.Query.Has("cursor") {
		decoded, err := base64.StdEncoding.DecodeString(entry.Query.Get("cursor"))
		if err != nil {
			s.respond(w, entry, http.StatusBadRequest, nil)
			return
		}
		offset, err = strconv.Atoi(string(decoded))
		if err != nil || offset < 0 || offset >= len(s.data) {
			s.respond(w, entry, http.StatusBadRequest, nil)
			return
		}
	}

	end := offset + pageSize
	if end > len(s.data) {
		end = len(s.data)
	}
	payload := struct {
		Results    []incident `json:"results"`
		TotalCount int        `json:"total_count"`
		Cursor     string     `json:"cursor,omitempty"`
	}{
		Results:    s.data[offset:end],
		TotalCount: len(s.data),
	}
	if end < len(s.data) {
		payload.Cursor = base64.StdEncoding.EncodeToString([]byte(strconv.Itoa(end)))
	}
	token.successfulListUse++
	s.respond(w, entry, http.StatusOK, payload)
}

func (s *Server) respond(w http.ResponseWriter, entry *Entry, status int, payload any) {
	entry.Status = status
	s.log = append(s.log, *entry)
	if payload != nil {
		w.Header().Set("Content-Type", "application/json")
	}
	w.WriteHeader(status)
	if payload != nil {
		_ = json.NewEncoder(w).Encode(payload)
	}
}

// EntriesFor filters a request-log snapshot by operationId.
func EntriesFor(log []Entry, operationID string) []Entry {
	var result []Entry
	for _, entry := range log {
		if entry.OperationID == operationID {
			result = append(result, entry)
		}
	}
	return result
}

// CursorOffset decodes a mock cursor for verifier diagnostics.
func CursorOffset(value string) (int, error) {
	decoded, err := base64.StdEncoding.DecodeString(value)
	if err != nil {
		return 0, err
	}
	return strconv.Atoi(strings.TrimSpace(string(decoded)))
}
