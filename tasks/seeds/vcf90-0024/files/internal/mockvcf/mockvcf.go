// Package mockvcf is a loopback stand-in for the SDDC Manager endpoints named by
// docs/contract.json. It is protected fixture code: do not edit it.
//
// The router is built from docs/contract.json at start-up, so the mock serves
// exactly the operations that contract names and nothing else. Anything else is
// answered 404 and still recorded, which lets a test assert that a client stayed
// inside the contract.
//
// Every request is captured in an ordered in-memory log that tests read through
// Requests, RequestsFor and OperationSequence. Set MOCKVCF_REQUEST_LOG to also
// mirror the log to a JSONL file.
package mockvcf

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

// Token is the bearer token the mock accepts. Anything else gets a 401.
const Token = "sddc-mock-access-token"

// Request is one captured inbound request.
type Request struct {
	// OperationID is the contract operationId that matched, or "" when the
	// request fell outside the contract and was answered 404.
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
	// RawQuery is the query string exactly as it arrived, undecoded.
	RawQuery string `json:"rawQuery"`
	// Query is RawQuery parsed. A parameter that was not sent is absent from
	// this map; a parameter sent with an empty value is present with "".
	Query map[string][]string `json:"query"`
	// Accept, ContentType and Authorization are captured verbatim.
	Accept        string `json:"accept"`
	ContentType   string `json:"contentType"`
	Authorization string `json:"authorization"`
	// Body is the raw request body, nil when there was none.
	Body []byte `json:"body,omitempty"`
	// Status is the response status the mock produced.
	Status int `json:"status"`
	// PathVars holds the contract path-template variables that matched.
	PathVars map[string]string `json:"pathVars,omitempty"`
}

// BodyJSON unmarshals the captured body into v.
func (r Request) BodyJSON(v any) error { return json.Unmarshal(r.Body, v) }

type route struct {
	opID   string
	method string
	segs   []string // a segment shaped {name} is a wildcard
}

// Server is a running mock. Close is registered with the test automatically.
type Server struct {
	http   *httptest.Server
	routes []route
	// terminalStatus is the second status-poll response. The normal fixture
	// succeeds; a protected negative-path test selects failure explicitly.
	terminalStatus string

	mu   sync.Mutex
	reqs []Request
	// pollCount tracks getSupportBundleStatus calls per bundle id so the first
	// poll reports IN_PROGRESS and later polls report a terminal status.
	pollCount map[string]int
	logFile   *os.File
}

type contractDoc struct {
	Operations []struct {
		OperationID string `json:"operationId"`
		Method      string `json:"method"`
		Path        string `json:"path"`
	} `json:"operations"`
}

// ContractPath resolves docs/contract.json by walking up from the working
// directory to the module root.
func ContractPath(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatalf("mockvcf: getwd: %v", err)
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return filepath.Join(dir, "docs", "contract.json")
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Fatal("mockvcf: could not find go.mod above the working directory")
		}
		dir = parent
	}
}

// Start builds the normal successful mock from docs/contract.json.
func Start(t *testing.T) *Server {
	t.Helper()
	return start(t, "COMPLETED_WITH_SUCCESS")
}

// StartWithTerminalStatus builds a mock whose second bundle poll returns the
// supplied terminal status. It is used to exercise terminal failure handling.
func StartWithTerminalStatus(t *testing.T, status string) *Server {
	t.Helper()
	return start(t, status)
}

func start(t *testing.T, terminalStatus string) *Server {
	t.Helper()

	raw, err := os.ReadFile(ContractPath(t))
	if err != nil {
		t.Fatalf("mockvcf: read contract: %v", err)
	}
	var doc contractDoc
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("mockvcf: parse contract: %v", err)
	}
	if len(doc.Operations) == 0 {
		t.Fatal("mockvcf: contract names no operations")
	}

	s := &Server{pollCount: map[string]int{}, terminalStatus: terminalStatus}
	for _, op := range doc.Operations {
		if _, ok := handlers[op.OperationID]; !ok {
			t.Fatalf("mockvcf: contract names operationId %q, which this mock does not serve", op.OperationID)
		}
		s.routes = append(s.routes, route{
			opID:   op.OperationID,
			method: strings.ToUpper(op.Method),
			segs:   splitPath(op.Path),
		})
	}

	if p := os.Getenv("MOCKVCF_REQUEST_LOG"); p != "" {
		f, err := os.Create(p)
		if err != nil {
			t.Fatalf("mockvcf: open request log: %v", err)
		}
		s.logFile = f
	}

	s.http = httptest.NewServer(http.HandlerFunc(s.serve))
	t.Cleanup(func() {
		s.http.Close()
		if s.logFile != nil {
			_ = s.logFile.Close()
		}
	})
	return s
}

// URL is the loopback base URL, with no trailing slash.
func (s *Server) URL() string { return s.http.URL }

// Requests returns a copy of the request log in arrival order.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]Request(nil), s.reqs...)
}

// RequestsFor returns the requests that matched one contract operation.
func (s *Server) RequestsFor(opID string) []Request {
	var out []Request
	for _, r := range s.Requests() {
		if r.OperationID == opID {
			out = append(out, r)
		}
	}
	return out
}

// OperationSequence returns the operationIds in arrival order. A request that
// fell outside the contract appears as "<off-contract GET /some/path>".
func (s *Server) OperationSequence() []string {
	var out []string
	for _, r := range s.Requests() {
		if r.OperationID == "" {
			out = append(out, fmt.Sprintf("<off-contract %s %s>", r.Method, r.Path))
			continue
		}
		out = append(out, r.OperationID)
	}
	return out
}

func splitPath(p string) []string {
	return strings.Split(strings.Trim(p, "/"), "/")
}

func matchSegs(tmpl, got []string) (map[string]string, bool) {
	if len(tmpl) != len(got) {
		return nil, false
	}
	vars := map[string]string{}
	for i, seg := range tmpl {
		if strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}") {
			if got[i] == "" {
				return nil, false
			}
			vars[seg[1:len(seg)-1]] = got[i]
			continue
		}
		if seg != got[i] {
			return nil, false
		}
	}
	return vars, true
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	body, _ := readAll(r)

	rec := Request{
		Method:        r.Method,
		Path:          r.URL.Path,
		RawQuery:      r.URL.RawQuery,
		Query:         map[string][]string{},
		Accept:        r.Header.Get("Accept"),
		ContentType:   r.Header.Get("Content-Type"),
		Authorization: r.Header.Get("Authorization"),
		Body:          body,
	}
	for k, v := range r.URL.Query() {
		rec.Query[k] = v
	}

	got := splitPath(r.URL.Path)
	var matched *route
	var vars map[string]string
	pathKnown := false
	for i := range s.routes {
		v, ok := matchSegs(s.routes[i].segs, got)
		if !ok {
			continue
		}
		pathKnown = true
		if s.routes[i].method == r.Method {
			matched, vars = &s.routes[i], v
			break
		}
	}

	switch {
	case matched == nil && pathKnown:
		rec.Status = s.writeErr(w, http.StatusMethodNotAllowed, "METHOD_NOT_ALLOWED",
			fmt.Sprintf("%s is not a method this contract names for %s", r.Method, r.URL.Path))
	case matched == nil:
		rec.Status = s.writeErr(w, http.StatusNotFound, "NOT_FOUND",
			fmt.Sprintf("%s %s is outside docs/contract.json", r.Method, r.URL.Path))
	case rec.Authorization != "Bearer "+Token:
		rec.OperationID = matched.opID
		rec.PathVars = vars
		rec.Status = s.writeErr(w, http.StatusUnauthorized, "UNAUTHORIZED",
			"the request carried no usable Authorization: Bearer credential")
	default:
		rec.OperationID = matched.opID
		rec.PathVars = vars
		rec.Status = handlers[matched.opID](s, w, r, vars, body)
	}

	s.record(rec)
}

func readAll(r *http.Request) ([]byte, error) {
	if r.Body == nil {
		return nil, nil
	}
	var buf bytes.Buffer
	if _, err := buf.ReadFrom(r.Body); err != nil {
		return nil, err
	}
	if buf.Len() == 0 {
		return nil, nil
	}
	return buf.Bytes(), nil
}

func (s *Server) record(rec Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.reqs = append(s.reqs, rec)
	if s.logFile != nil {
		line, err := json.Marshal(rec)
		if err == nil {
			_, _ = s.logFile.Write(append(line, '\n'))
		}
	}
}

func (s *Server) nextPoll(id string) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pollCount[id]++
	return s.pollCount[id]
}

func (s *Server) writeJSON(w http.ResponseWriter, status int, v any) int {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
	return status
}

func (s *Server) writeErr(w http.ResponseWriter, status int, code, msg string) int {
	return s.writeJSON(w, status, map[string]any{
		"errorCode": code,
		"errorType": "MOCK",
		"message":   msg,
	})
}
