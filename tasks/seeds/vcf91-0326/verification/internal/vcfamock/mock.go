// Package vcfamock is a loopback stand-in for the VCF Automation policy
// endpoints, pinned to docs/contract.json.
//
// The mock reads the contract at construction and routes from it: it serves
// exactly the operations the contract names, at exactly the methods and paths
// the contract records for them, and answers anything else with 404 while
// recording the attempt as rejected. It refuses to start if the contract names
// an operation it has no handler for, so the two cannot drift apart.
//
// Every request is recorded - method, path, query, headers, raw body and the
// status that came back - and the log is readable from the test.
package vcfamock

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
)

// Recorded is one request as the mock saw it.
type Recorded struct {
	// Operation is the contract operation id this request matched, or "" when
	// it matched none.
	Operation string
	Method    string
	Path      string
	RawQuery  string
	Header    http.Header
	Body      []byte
	// Status is the status the mock returned, or 0 when the connection was
	// dropped without a response.
	Status int
	// Dropped is true when the mock closed the connection instead of
	// answering, so the caller never observed an outcome.
	Dropped bool
	// Rejected is true when the request did not match any operation the
	// contract names.
	Rejected bool
}

// Action scripts one upsertPolicy attempt. Actions are consumed in order, one
// per POST; once the script runs out, the mock falls back to its ordinary
// upsert behaviour.
type Action struct {
	// Status replaces the ordinary outcome. Zero means "behave normally".
	Status int
	// Body is returned with Status when Status is set.
	Body string
	// Commit applies the write before responding or dropping. It models a
	// server that did the work and then failed to report it.
	Commit bool
	// Drop closes the connection without writing a response.
	Drop bool
	// ReadStatus, when nonzero, replaces the response to subsequent getPolicy
	// calls. ReadBody is returned with it. These fields let tests exercise
	// read-back failures after an otherwise successful upsert.
	ReadStatus int
	ReadBody   string
	// After runs once the response has been written or the connection closed.
	After func()
}

type operation struct {
	id       string
	method   string
	segments []string
}

// Mock is a loopback VCF Automation policy service.
type Mock struct {
	t      *testing.T
	server *httptest.Server
	ops    []operation

	mu         sync.Mutex
	log        []Recorded
	policies   map[string]map[string]any
	script     []Action
	posts      int
	clock      int
	readStatus int
	readBody   string
}

type contractFile struct {
	Operations []struct {
		ID     string `json:"id"`
		Method string `json:"method"`
		Path   string `json:"path"`
	} `json:"operations"`
}

// New starts a mock pinned to the contract at contractPath and scripted with
// the given upsert actions. It is stopped when the test finishes.
func New(t *testing.T, contractPath string, script ...Action) *Mock {
	t.Helper()

	raw, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("vcfamock: read contract: %v", err)
	}
	var parsed contractFile
	if err := json.Unmarshal(raw, &parsed); err != nil {
		t.Fatalf("vcfamock: parse contract: %v", err)
	}
	if len(parsed.Operations) == 0 {
		t.Fatalf("vcfamock: contract %s names no operations", contractPath)
	}

	m := &Mock{t: t, policies: map[string]map[string]any{}, script: script}
	for _, op := range parsed.Operations {
		switch op.ID {
		case "upsertPolicy", "getPolicy":
		default:
			t.Fatalf("vcfamock: contract names operation %q, which this mock does not serve", op.ID)
		}
		m.ops = append(m.ops, operation{
			id:       op.ID,
			method:   op.Method,
			segments: strings.Split(strings.Trim(op.Path, "/"), "/"),
		})
	}

	m.server = httptest.NewServer(http.HandlerFunc(m.handle))
	t.Cleanup(m.server.Close)
	return m
}

// URL is the base URL of the running mock.
func (m *Mock) URL() string { return m.server.URL }

// Requests returns the request log in arrival order.
func (m *Mock) Requests() []Recorded {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]Recorded, len(m.log))
	copy(out, m.log)
	return out
}

// Operations returns the contract operation id of each logged request, in
// order, using "<rejected>" for requests that matched no operation.
func (m *Mock) Operations() []string {
	var out []string
	for _, entry := range m.Requests() {
		if entry.Rejected {
			out = append(out, "<rejected>")
			continue
		}
		out = append(out, entry.Operation)
	}
	return out
}

// Policies returns a copy of the stored policies, keyed by policy id.
func (m *Mock) Policies() map[string]map[string]any {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make(map[string]map[string]any, len(m.policies))
	for id, policy := range m.policies {
		clone := make(map[string]any, len(policy))
		for key, value := range policy {
			clone[key] = value
		}
		out[id] = clone
	}
	return out
}

// Seed stores a policy as if it were already there. The policy must carry an
// "id".
func (m *Mock) Seed(policy map[string]any) {
	m.t.Helper()
	id, _ := policy["id"].(string)
	if id == "" {
		m.t.Fatalf("vcfamock: seeded policy has no id")
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	clone := make(map[string]any, len(policy))
	for key, value := range policy {
		clone[key] = value
	}
	m.policies[id] = clone
}

func (m *Mock) route(method, path string) (id string, capturedID string, ok bool) {
	got := strings.Split(strings.Trim(path, "/"), "/")
	for _, op := range m.ops {
		if !strings.EqualFold(op.method, method) || len(op.segments) != len(got) {
			continue
		}
		captured := ""
		matched := true
		for i, want := range op.segments {
			if strings.HasPrefix(want, "{") && strings.HasSuffix(want, "}") {
				captured = got[i]
				continue
			}
			if want != got[i] {
				matched = false
				break
			}
		}
		if matched {
			return op.id, captured, true
		}
	}
	return "", "", false
}

func (m *Mock) handle(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	entry := Recorded{
		Method:   r.Method,
		Path:     r.URL.Path,
		RawQuery: r.URL.RawQuery,
		Header:   r.Header.Clone(),
		Body:     body,
	}

	operationID, policyID, ok := m.route(r.Method, r.URL.Path)
	if !ok {
		entry.Rejected = true
		entry.Status = http.StatusNotFound
		m.record(entry)
		w.WriteHeader(http.StatusNotFound)
		return
	}
	entry.Operation = operationID

	switch operationID {
	case "upsertPolicy":
		m.upsert(w, entry)
	case "getPolicy":
		m.get(w, entry, policyID)
	}
}

func (m *Mock) upsert(w http.ResponseWriter, entry Recorded) {
	m.mu.Lock()
	action := Action{}
	if m.posts < len(m.script) {
		action = m.script[m.posts]
	}
	m.posts++
	if action.ReadStatus != 0 {
		m.readStatus = action.ReadStatus
		m.readBody = action.ReadBody
	}

	var document map[string]any
	if err := json.Unmarshal(entry.Body, &document); err != nil {
		document = nil
	}
	typeID, _ := document["typeId"].(string)

	commit := action.Commit
	status := action.Status
	if !action.Drop && status == 0 {
		commit = true
	}

	created := false
	if commit && typeID != "" {
		created = m.applyLocked(document)
	}
	if status == 0 && !action.Drop {
		if typeID == "" {
			status = http.StatusBadRequest
		} else if created {
			status = http.StatusCreated
		} else {
			status = http.StatusOK
		}
	}
	if action.Drop {
		entry.Dropped = true
		entry.Status = 0
	} else {
		entry.Status = status
	}
	m.log = append(m.log, entry)
	m.mu.Unlock()

	if action.Drop {
		m.dropConnection(w)
		if action.After != nil {
			action.After()
		}
		return
	}

	// The reference documents no response data structure for this operation at
	// any status, so the mock answers with the status alone.
	if action.Body != "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		_, _ = io.WriteString(w, action.Body)
	} else {
		w.WriteHeader(status)
	}
	if action.After != nil {
		action.After()
	}
}

// applyLocked stores the document and reports whether it created a policy. A
// document with no id gets a server-minted one, which is how a client that
// leaves the id to the server ends up with a duplicate per delivery.
func (m *Mock) applyLocked(document map[string]any) bool {
	id, _ := document["id"].(string)
	if id == "" {
		id = fmt.Sprintf("server-minted-%d", len(m.policies)+1)
	}
	m.clock++
	stamp := fmt.Sprintf("2026-03-04T09:%02d:00Z", m.clock)

	stored := make(map[string]any, len(document)+6)
	for key, value := range document {
		stored[key] = value
	}
	stored["id"] = id
	if _, ok := stored["enforcementType"]; !ok {
		stored["enforcementType"] = "HARD"
	}
	stored["orgId"] = "8f4a2c5e-0d31-4a7b-9a2f-6c1d0e3b7a55"
	stored["lastUpdatedAt"] = stamp
	stored["lastUpdatedBy"] = "svc-automation@vcf.local"
	stored["statistics"] = map[string]any{
		"enforcedCount": 0, "notEnforcedCount": 0, "conflictCount": 0,
	}

	existing, found := m.policies[id]
	if found {
		stored["createdAt"] = existing["createdAt"]
		stored["createdBy"] = existing["createdBy"]
	} else {
		stored["createdAt"] = stamp
		stored["createdBy"] = "svc-automation@vcf.local"
	}
	m.policies[id] = stored
	return !found
}

func (m *Mock) get(w http.ResponseWriter, entry Recorded, policyID string) {
	m.mu.Lock()
	if m.readStatus != 0 {
		status := m.readStatus
		body := m.readBody
		entry.Status = status
		m.log = append(m.log, entry)
		m.mu.Unlock()
		if body != "" {
			w.Header().Set("Content-Type", "application/json")
		}
		w.WriteHeader(status)
		_, _ = io.WriteString(w, body)
		return
	}
	policy, found := m.policies[policyID]
	var encoded []byte
	if found {
		encoded, _ = json.Marshal(policy)
		entry.Status = http.StatusOK
	} else {
		entry.Status = http.StatusNotFound
	}
	m.log = append(m.log, entry)
	m.mu.Unlock()

	if !found {
		w.WriteHeader(http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(encoded)
}

func (m *Mock) dropConnection(w http.ResponseWriter) {
	hijacker, ok := w.(http.Hijacker)
	if !ok {
		m.t.Errorf("vcfamock: response writer cannot be hijacked")
		return
	}
	conn, _, err := hijacker.Hijack()
	if err != nil {
		m.t.Errorf("vcfamock: hijack: %v", err)
		return
	}
	_ = conn.Close()
}

func (m *Mock) record(entry Recorded) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.log = append(m.log, entry)
}
