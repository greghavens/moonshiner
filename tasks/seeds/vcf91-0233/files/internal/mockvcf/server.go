// Package mockvcf provides a loopback-only mock of the VCF 9.1 SDDC LCM
// service. Its routing table is loaded from docs/contract.json at construction
// time: it serves exactly the operations that the contract names, and rejects
// anything else. Every request it receives is appended to a request log that
// tests can read back.
//
// This package is a protected test fixture. It never contacts a live VMware
// endpoint and binds only to 127.0.0.1.
package mockvcf

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

// Contract is the subset of docs/contract.json that the mock is pinned to.
type Contract struct {
	Source struct {
		Repository string `json:"repository"`
		CommitSHA  string `json:"commit_sha"`
		SpecPath   string `json:"spec_path"`
		APIVersion string `json:"api_version"`
	} `json:"source"`
	ServerBasePath string `json:"server_base_path"`
	Security       struct {
		Header      string `json:"header"`
		ValuePrefix string `json:"value_prefix"`
	} `json:"security"`
	Operations []ContractOperation `json:"operations"`
	Pagination struct {
		PageNumberParam string `json:"page_number_param"`
		PageSizeParam   string `json:"page_size_param"`
		MaxPageSize     int    `json:"max_page_size"`
		FirstPageNumber int    `json:"first_page_number"`
	} `json:"pagination"`
}

// ContractOperation is one entry of the contract's operation list.
type ContractOperation struct {
	OperationID     string `json:"operationId"`
	Method          string `json:"method"`
	Path            string `json:"path"`
	QueryParameters []struct {
		Name string   `json:"name"`
		Enum []string `json:"enum"`
	} `json:"query_parameters"`
}

func (o ContractOperation) allowedQuery() map[string]map[string]bool {
	allowed := make(map[string]map[string]bool, len(o.QueryParameters))
	for _, p := range o.QueryParameters {
		var values map[string]bool
		if len(p.Enum) > 0 {
			values = make(map[string]bool, len(p.Enum))
			for _, v := range p.Enum {
				values[v] = true
			}
		}
		allowed[p.Name] = values
	}
	return allowed
}

// LoadContract reads and parses the local REST contract.
func LoadContract(t *testing.T, path string) Contract {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("mockvcf: cannot read contract %s: %v", path, err)
	}
	var c Contract
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatalf("mockvcf: cannot parse contract %s: %v", path, err)
	}
	if len(c.Operations) == 0 {
		t.Fatalf("mockvcf: contract %s names no operations", path)
	}
	return c
}

// Request is one recorded inbound HTTP request.
type Request struct {
	Seq         int
	OperationID string // "" when the request matched no contract operation
	Method      string
	Path        string
	RawQuery    string
	Query       map[string][]string
	Authz       string
	Accept      string
	ContentType string
	BodyLen     int
	Status      int
}

// PageNumber returns the pageNumber query value, or -1 when it was not sent.
func (r Request) PageNumber() int {
	v, ok := r.Query["pageNumber"]
	if !ok || len(v) != 1 {
		return -1
	}
	n, err := strconv.Atoi(v[0])
	if err != nil {
		return -1
	}
	return n
}

// SortedQuery renders the query as a deterministic "key=value" list. Keys are
// sorted; a key sent with an empty value renders as "key=" so that tests can
// distinguish "omitted" from "sent empty".
func (r Request) SortedQuery() []string {
	out := make([]string, 0, len(r.Query))
	for k, vs := range r.Query {
		for _, v := range vs {
			out = append(out, k+"="+v)
		}
	}
	sort.Strings(out)
	return out
}

// Config configures a mock server instance.
type Config struct {
	// Token is the bearer token the server requires.
	Token string
	// Pages holds the task elements for page 0, page 1, ... in order.
	Pages [][]map[string]any
	// TotalElements is reported in pageMetadata. Zero means "sum of Pages".
	TotalElements int
	// Components is served by getComponents.
	Components []map[string]any
	// RequireConcurrentFollowers, when > 0, makes every request for a page
	// after the first block until that many follower-page requests are in
	// flight at once. A client that walks the pages one at a time therefore
	// stalls and receives 503 instead of a page.
	RequireConcurrentFollowers int
	// FailPage maps a page number to an HTTP status to return instead of a page.
	FailPage map[int]int
	// BarrierTimeout bounds the follower-page barrier wait. Zero means 3s.
	BarrierTimeout time.Duration
}

// Server is a loopback mock of the SDDC LCM service.
type Server struct {
	contract Contract
	cfg      Config
	http     *httptest.Server

	mu       sync.Mutex
	requests []Request

	barrierMu      sync.Mutex
	barrierInFlite int
	barrierOpen    bool
	barrierC       chan struct{}
}

// New starts a mock pinned to the contract at contractPath. The server is
// stopped automatically when the test finishes.
func New(t *testing.T, contractPath string, cfg Config) *Server {
	t.Helper()
	contract := LoadContract(t, contractPath)
	if cfg.BarrierTimeout == 0 {
		cfg.BarrierTimeout = 3 * time.Second
	}
	s := &Server{contract: contract, cfg: cfg, barrierC: make(chan struct{})}

	mux := http.NewServeMux()
	registered := make(map[string]bool)
	for _, op := range contract.Operations {
		op := op
		route := contract.ServerBasePath + op.Path
		if strings.Contains(op.Path, "{") {
			// Templated paths are not part of this fixture's contract surface.
			continue
		}
		if registered[route] {
			continue
		}
		registered[route] = true
		mux.HandleFunc(route, func(w http.ResponseWriter, r *http.Request) {
			s.serveOperation(w, r, op)
		})
	}
	// Anything the contract does not name is logged and refused.
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		s.record(r, ContractOperation{}, http.StatusNotFound)
		writeError(w, http.StatusNotFound, "operation is not named by the contract")
	})

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("mockvcf: cannot listen on loopback: %v", err)
	}
	s.http = &httptest.Server{
		Listener: listener,
		Config:   &http.Server{Handler: mux},
	}
	s.http.Start()
	t.Cleanup(s.http.Close)
	return s
}

// BaseURL is the loopback URL including the contract's server base path.
func (s *Server) BaseURL() string {
	return s.http.URL + s.contract.ServerBasePath
}

// Requests returns a copy of the request log in arrival order.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.requests))
	copy(out, s.requests)
	return out
}

// RequestsFor returns the recorded requests for one contract operation.
func (s *Server) RequestsFor(operationID string) []Request {
	var out []Request
	for _, r := range s.Requests() {
		if r.OperationID == operationID {
			out = append(out, r)
		}
	}
	return out
}

func (s *Server) record(r *http.Request, op ContractOperation, status int) {
	query := make(map[string][]string, len(r.URL.Query()))
	for k, v := range r.URL.Query() {
		vs := make([]string, len(v))
		copy(vs, v)
		query[k] = vs
	}
	body := 0
	if r.ContentLength > 0 {
		body = int(r.ContentLength)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requests = append(s.requests, Request{
		Seq:         len(s.requests),
		OperationID: op.OperationID,
		Method:      r.Method,
		Path:        r.URL.Path,
		RawQuery:    r.URL.RawQuery,
		Query:       query,
		Authz:       r.Header.Get("Authorization"),
		Accept:      r.Header.Get("Accept"),
		ContentType: r.Header.Get("Content-Type"),
		BodyLen:     body,
		Status:      status,
	})
}

func (s *Server) serveOperation(w http.ResponseWriter, r *http.Request, op ContractOperation) {
	status, payload := s.handle(r, op)
	s.record(r, op, status)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func (s *Server) handle(r *http.Request, op ContractOperation) (int, any) {
	if r.Method != op.Method {
		return http.StatusMethodNotAllowed, errorBody(fmt.Sprintf(
			"%s expects %s, got %s", op.OperationID, op.Method, r.Method))
	}
	want := s.contract.Security.ValuePrefix + s.cfg.Token
	if got := r.Header.Get(s.contract.Security.Header); got != want {
		return http.StatusUnauthorized, errorBody("missing or malformed bearer credential")
	}

	allowed := op.allowedQuery()
	if op.OperationID == s.paginatedOperationID() {
		allowed[s.contract.Pagination.PageNumberParam] = nil
		allowed[s.contract.Pagination.PageSizeParam] = nil
	}
	for key, values := range r.URL.Query() {
		enum, ok := allowed[key]
		if !ok {
			return http.StatusBadRequest, errorBody(fmt.Sprintf(
				"%s is not a query parameter of %s", key, op.OperationID))
		}
		if len(values) != 1 {
			return http.StatusBadRequest, errorBody(fmt.Sprintf(
				"%s was sent %d times", key, len(values)))
		}
		if values[0] == "" {
			return http.StatusBadRequest, errorBody(fmt.Sprintf(
				"optional parameter %s was sent empty; unset parameters must be omitted", key))
		}
		if enum != nil && !enum[values[0]] {
			return http.StatusBadRequest, errorBody(fmt.Sprintf(
				"%s=%s is not an allowed value", key, values[0]))
		}
	}

	switch op.OperationID {
	case "getTasks":
		return s.handleTasks(r)
	case "getComponents":
		return s.handleComponents(r)
	default:
		return http.StatusNotImplemented, errorBody("no fixture for " + op.OperationID)
	}
}

func (s *Server) paginatedOperationID() string { return "getTasks" }

func (s *Server) handleTasks(r *http.Request) (int, any) {
	q := r.URL.Query()
	pageNumber := s.contract.Pagination.FirstPageNumber
	if raw := q.Get(s.contract.Pagination.PageNumberParam); raw != "" {
		n, err := strconv.Atoi(raw)
		if err != nil || n < s.contract.Pagination.FirstPageNumber {
			return http.StatusBadRequest, errorBody("pageNumber must be a non-negative integer")
		}
		pageNumber = n
	}
	pageSize := s.contract.Pagination.MaxPageSize
	if raw := q.Get(s.contract.Pagination.PageSizeParam); raw != "" {
		n, err := strconv.Atoi(raw)
		if err != nil || n < 1 {
			return http.StatusBadRequest, errorBody("pageSize must be a positive integer")
		}
		if n > s.contract.Pagination.MaxPageSize {
			return http.StatusBadRequest, errorBody(fmt.Sprintf(
				"pageSize %d exceeds the maximum of %d", n, s.contract.Pagination.MaxPageSize))
		}
		pageSize = n
	}

	if pageNumber > s.contract.Pagination.FirstPageNumber {
		if !s.awaitFollowers() {
			return http.StatusServiceUnavailable, errorBody(
				"follower pages must be requested concurrently; only one was in flight")
		}
	}
	if status, ok := s.cfg.FailPage[pageNumber]; ok {
		return status, errorBody(fmt.Sprintf("page %d is unavailable", pageNumber))
	}

	elements := []map[string]any{}
	if pageNumber < len(s.cfg.Pages) {
		elements = s.cfg.Pages[pageNumber]
	}
	total := s.cfg.TotalElements
	if total == 0 {
		for _, p := range s.cfg.Pages {
			total += len(p)
		}
	}
	return http.StatusOK, map[string]any{
		"elements": elements,
		"pageMetadata": map[string]any{
			"pageNumber":    pageNumber,
			"pageSize":      pageSize,
			"totalElements": total,
			"totalPages":    len(s.cfg.Pages),
		},
	}
}

// awaitFollowers implements the concurrency barrier. It reports false when the
// required number of follower-page requests never overlapped.
func (s *Server) awaitFollowers() bool {
	if s.cfg.RequireConcurrentFollowers <= 0 {
		return true
	}
	s.barrierMu.Lock()
	if s.barrierOpen {
		s.barrierMu.Unlock()
		return true
	}
	s.barrierInFlite++
	wait := s.barrierC
	if s.barrierInFlite >= s.cfg.RequireConcurrentFollowers {
		s.barrierOpen = true
		close(s.barrierC)
		s.barrierMu.Unlock()
		return true
	}
	s.barrierMu.Unlock()

	timer := time.NewTimer(s.cfg.BarrierTimeout)
	defer timer.Stop()
	select {
	case <-wait:
		return true
	case <-timer.C:
		s.barrierMu.Lock()
		s.barrierInFlite--
		s.barrierMu.Unlock()
		return false
	}
}

func (s *Server) handleComponents(r *http.Request) (int, any) {
	scope := r.URL.Query().Get("scope")
	out := []map[string]any{}
	for _, c := range s.cfg.Components {
		if scope == "" || c["scope"] == scope {
			out = append(out, c)
		}
	}
	return http.StatusOK, map[string]any{"components": out}
}

func errorBody(message string) map[string]any {
	return map[string]any{
		"errorCode":      "MOCK_CONTRACT_VIOLATION",
		"message":        message,
		"referenceToken": "mockvcf",
	}
}

func writeError(w http.ResponseWriter, status int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(errorBody(message))
}
