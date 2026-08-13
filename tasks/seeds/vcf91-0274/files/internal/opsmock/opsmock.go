// Package opsmock provides a loopback HTTP server pinned to docs/contract.json.
//
// The server refuses to route anything the contract does not name, records every
// request it receives (matched or not) in an ordered log, and answers matched
// operations from a fixture set under fixtures/. It exists so that tests can
// assert the exact wire shape a client puts on the network without contacting a
// VMware endpoint.
package opsmock

import (
	"embed"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
)

//go:embed fixtures
var fixtureFS embed.FS

// Token is the credential the mock accepts. It is presented in the
// Authorization header using the scheme named by the contract.
const Token = "b7f0c2a4-3e51-4d88-9a06-15c7be2d9f34"

// Request is one recorded inbound request.
type Request struct {
	// OperationID is the contract operationId that matched, or "" when the
	// request did not correspond to any operation the contract names.
	OperationID string
	Method      string
	// Path is the full request path, including the contract base path.
	Path     string
	RawQuery string
	Query    url.Values
	Header   http.Header
	// Body holds the exact bytes received; it is nil when no body was sent.
	Body []byte
	// Status is the response status the mock returned.
	Status int
}

type operation struct {
	id       string
	method   string
	segments []string // path template split on "/", "{name}" matches one segment
}

type contract struct {
	BasePath      string `json:"basePath"`
	Authorization struct {
		Header string `json:"header"`
		Scheme string `json:"scheme"`
	} `json:"authorization"`
	Operations map[string]struct {
		Method string `json:"method"`
		Path   string `json:"path"`
	} `json:"operations"`
}

// Server is a loopback VCF Operations stand-in pinned to a contract.
type Server struct {
	srv      *httptest.Server
	basePath string
	authHdr  string
	authVal  string
	ops      []operation
	fixtures string

	mu  sync.Mutex
	log []Request
	// forcedStatus is test-controlled response state. It is empty in normal
	// operation, where every matched request is answered from its fixture.
	forcedStatus map[string]int
}

// ContractPath returns the absolute path of docs/contract.json in this
// repository, so callers do not depend on the working directory.
func ContractPath() string {
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		return "docs/contract.json"
	}
	return filepath.Join(filepath.Dir(file), "..", "..", "docs", "contract.json")
}

// FixtureSets lists the available incident fixture sets, sorted.
func FixtureSets() []string {
	entries, err := fs.ReadDir(fixtureFS, "fixtures")
	if err != nil {
		return nil
	}
	var out []string
	for _, e := range entries {
		if e.IsDir() {
			out = append(out, e.Name())
		}
	}
	sort.Strings(out)
	return out
}

// New starts a loopback server that serves only the operations named by the
// contract at contractPath, answering from the named fixture set. Callers must
// Close the returned Server.
func New(contractPath, fixtureSet string) (*Server, error) {
	raw, err := os.ReadFile(contractPath)
	if err != nil {
		return nil, fmt.Errorf("opsmock: read contract: %w", err)
	}
	var c contract
	if err := json.Unmarshal(raw, &c); err != nil {
		return nil, fmt.Errorf("opsmock: parse contract: %w", err)
	}
	if len(c.Operations) == 0 {
		return nil, fmt.Errorf("opsmock: contract names no operations")
	}
	if _, err := fs.Stat(fixtureFS, "fixtures/"+fixtureSet); err != nil {
		return nil, fmt.Errorf("opsmock: unknown fixture set %q", fixtureSet)
	}

	s := &Server{
		basePath: strings.TrimSuffix(c.BasePath, "/"),
		authHdr:  c.Authorization.Header,
		authVal:  c.Authorization.Scheme + " " + Token,
		fixtures: fixtureSet,
	}
	for id, op := range c.Operations {
		s.ops = append(s.ops, operation{
			id:       id,
			method:   strings.ToUpper(op.Method),
			segments: strings.Split(strings.TrimPrefix(op.Path, "/"), "/"),
		})
	}
	sort.Slice(s.ops, func(i, j int) bool { return s.ops[i].id < s.ops[j].id })
	s.srv = httptest.NewServer(http.HandlerFunc(s.handle))
	return s, nil
}

// URL is the loopback base URL of the server, without the contract base path.
func (s *Server) URL() string { return s.srv.URL }

// Close shuts the server down.
func (s *Server) Close() { s.srv.Close() }

// Requests returns a snapshot of the request log in arrival order.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.log))
	copy(out, s.log)
	return out
}

// OperationIDs returns the operationId of each logged request in arrival order.
// Unmatched requests contribute an empty string.
func (s *Server) OperationIDs() []string {
	reqs := s.Requests()
	out := make([]string, 0, len(reqs))
	for _, r := range reqs {
		out = append(out, r.OperationID)
	}
	return out
}

// Reset clears the request log.
func (s *Server) Reset() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log = nil
}

// ForceStatus makes operationID return status instead of its JSON fixture.
// It is used by the protected suite to verify that callers reject every
// non-success response. Call it before issuing requests to the server.
func (s *Server) ForceStatus(operationID string, status int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.forcedStatus == nil {
		s.forcedStatus = make(map[string]int)
	}
	s.forcedStatus[operationID] = status
}

func (s *Server) statusFor(operationID string) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.forcedStatus[operationID]
}

func (s *Server) record(r Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log = append(s.log, r)
}

func (s *Server) match(method, urlPath string) string {
	rest, ok := strings.CutPrefix(urlPath, s.basePath)
	if !ok || (rest != "" && !strings.HasPrefix(rest, "/")) {
		return ""
	}
	got := strings.Split(strings.TrimPrefix(rest, "/"), "/")
	for _, op := range s.ops {
		if op.method != method || len(op.segments) != len(got) {
			continue
		}
		hit := true
		for i, seg := range op.segments {
			if strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}") {
				if got[i] == "" {
					hit = false
					break
				}
				continue
			}
			if seg != got[i] {
				hit = false
				break
			}
		}
		if hit {
			return op.id
		}
	}
	return ""
}

func (s *Server) handle(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	_ = r.Body.Close()
	if len(body) == 0 {
		body = nil
	}

	rec := Request{
		Method:   r.Method,
		Path:     r.URL.Path,
		RawQuery: r.URL.RawQuery,
		Query:    r.URL.Query(),
		Header:   r.Header.Clone(),
		Body:     body,
	}
	rec.OperationID = s.match(r.Method, r.URL.Path)

	switch {
	case rec.OperationID == "":
		rec.Status = http.StatusNotFound
		s.record(rec)
		writeJSON(w, http.StatusNotFound, map[string]string{
			"message": "no operation in docs/contract.json matches " + r.Method + " " + r.URL.Path,
		})
		return
	case r.Header.Get(s.authHdr) != s.authVal:
		rec.Status = http.StatusUnauthorized
		s.record(rec)
		writeJSON(w, http.StatusUnauthorized, map[string]string{
			"message": "missing or malformed " + s.authHdr + " header",
		})
		return
	}
	payload, err := fixtureFS.ReadFile("fixtures/" + s.fixtures + "/" + rec.OperationID + ".json")
	if err != nil {
		rec.Status = http.StatusNotFound
		s.record(rec)
		writeJSON(w, http.StatusNotFound, map[string]string{
			"message": "fixture set " + s.fixtures + " has no data for " + rec.OperationID,
		})
		return
	}
	if status := s.statusFor(rec.OperationID); status != 0 {
		rec.Status = status
		s.record(rec)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = w.Write(payload)
		return
	}
	rec.Status = http.StatusOK
	s.record(rec)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(payload)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
