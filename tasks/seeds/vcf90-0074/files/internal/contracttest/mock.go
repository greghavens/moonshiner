package contracttest

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"sort"
	"strings"
	"sync"
	"testing"
)

// RequestLog is an immutable snapshot of one request received by Mock.
type RequestLog struct {
	Method   string
	Path     string
	RawQuery string
	Header   http.Header
	Body     []byte
}

type routeKey struct {
	method string
	path   string
}

type contractDocument struct {
	Servers []struct {
		URL string `json:"url"`
	} `json:"servers"`
	Paths map[string]map[string]struct {
		OperationID string `json:"operationId"`
	} `json:"paths"`
}

type wireGroup struct {
	ID            string  `json:"id,omitempty"`
	Name          string  `json:"name"`
	Description   *string `json:"description,omitempty"`
	CollectorIDs  []int32 `json:"collectorId,omitempty"`
	SystemDefined *bool   `json:"systemDefined,omitempty"`
	HAEnabled     *bool   `json:"haEnabled,omitempty"`
	LBEnabled     *bool   `json:"lbEnabled,omitempty"`
	VirtualIP     *string `json:"virtualIP,omitempty"`
}

type queuedResponse struct {
	status int
	body   string
}

// Mock is an in-memory, loopback-only VCF Operations server. Its routable
// method/path pairs are loaded from the candidate's reduced contract; any
// operation not named there returns 404. POST deliberately does not dedupe so
// tests detect clients that repeat the mutation.
type Mock struct {
	t        testing.TB
	server   *httptest.Server
	routes   map[routeKey]string
	mu       sync.Mutex
	logs     []RequestLog
	groups   []wireGroup
	nextID   int
	dropPost bool
	queued   map[string][]queuedResponse
}

// NewMock loads contractPath and starts a loopback server pinned to it.
func NewMock(t testing.TB, contractPath string) *Mock {
	t.Helper()
	b, err := os.ReadFile(contractPath)
	if err != nil {
		t.Fatalf("read contract: %v", err)
	}
	var doc contractDocument
	if err := json.Unmarshal(b, &doc); err != nil {
		t.Fatalf("decode contract: %v", err)
	}
	if len(doc.Servers) != 1 || doc.Servers[0].URL == "" {
		t.Fatalf("contract must contain exactly one non-empty server URL")
	}
	m := &Mock{
		t: t, routes: make(map[routeKey]string), nextID: 1,
		queued: make(map[string][]queuedResponse),
	}
	base := strings.TrimSuffix(doc.Servers[0].URL, "/")
	for path, item := range doc.Paths {
		for method, operation := range item {
			method = strings.ToUpper(method)
			m.routes[routeKey{method: method, path: base + path}] = operation.OperationID
		}
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("start loopback mock: %v", err)
	}
	m.server = httptest.NewUnstartedServer(http.HandlerFunc(m.serveHTTP))
	m.server.Listener = listener
	m.server.Start()
	t.Cleanup(m.server.Close)
	return m
}

// URL returns the loopback appliance origin.
func (m *Mock) URL() string { return m.server.URL }

// HTTPClient returns the transport configured for this loopback server.
func (m *Mock) HTTPClient() *http.Client { return m.server.Client() }

// DropNextCreateResponse makes the next create commit its effect and then
// close the HTTP/1 connection before sending a response.
func (m *Mock) DropNextCreateResponse() {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.dropPost = true
}

// QueueResponse makes the next request for operationID return a controlled
// response without applying the operation's normal behavior. It is used to
// exercise client handling of status and decode failures on the same
// contract-pinned routes as successful requests.
func (m *Mock) QueueResponse(operationID string, status int, body string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.queued[operationID] = append(m.queued[operationID], queuedResponse{
		status: status,
		body:   body,
	})
}

// Logs returns request-log snapshots in receive order.
func (m *Mock) Logs() []RequestLog {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]RequestLog, len(m.logs))
	for i, entry := range m.logs {
		out[i] = entry
		out[i].Header = entry.Header.Clone()
		out[i].Body = append([]byte(nil), entry.Body...)
	}
	return out
}

// GroupCount reports the number of mutation effects held by the mock.
func (m *Mock) GroupCount() int {
	m.mu.Lock()
	defer m.mu.Unlock()
	return len(m.groups)
}

func (m *Mock) serveHTTP(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	m.mu.Lock()
	m.logs = append(m.logs, RequestLog{
		Method: r.Method, Path: r.URL.Path, RawQuery: r.URL.RawQuery,
		Header: r.Header.Clone(), Body: append([]byte(nil), body...),
	})
	op, ok := m.routes[routeKey{method: r.Method, path: r.URL.Path}]
	if !ok {
		m.mu.Unlock()
		http.NotFound(w, r)
		return
	}
	if queued := m.queued[op]; len(queued) != 0 {
		response := queued[0]
		m.queued[op] = queued[1:]
		m.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(response.status)
		_, _ = io.WriteString(w, response.body)
		return
	}
	switch op {
	case "getCollectorGroups":
		groups := append([]wireGroup(nil), m.groups...)
		m.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"collectorGroups": groups})
	case "createCollectorGroup":
		var group wireGroup
		if err := json.Unmarshal(body, &group); err != nil {
			m.mu.Unlock()
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		group.ID = fmt.Sprintf("00000000-0000-0000-0000-%012d", m.nextID)
		m.nextID++
		m.groups = append(m.groups, group)
		drop := m.dropPost
		m.dropPost = false
		m.mu.Unlock()
		if drop {
			hijacker, ok := w.(http.Hijacker)
			if !ok {
				m.t.Errorf("loopback response writer cannot hijack connection")
				return
			}
			conn, _, err := hijacker.Hijack()
			if err != nil {
				m.t.Errorf("hijack create response: %v", err)
				return
			}
			_ = conn.Close()
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(group)
	default:
		m.mu.Unlock()
		http.NotFound(w, r)
	}
}

// OperationIDs returns the sorted operation IDs served by this instance.
func (m *Mock) OperationIDs() []string {
	ids := make([]string, 0, len(m.routes))
	for _, id := range m.routes {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}
