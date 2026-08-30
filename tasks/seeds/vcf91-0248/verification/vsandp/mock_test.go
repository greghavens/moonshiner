package vsandp

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync"
	"testing"
)

// This file is an in-memory stand-in for the vSAN Data Protection snapshot
// appliance. It serves only the four operations named in docs/contract.json and
// answers 404 for anything else, so a client that invents an endpoint is caught
// rather than quietly accommodated. Every request is recorded verbatim for the
// wire-shape assertions in wire_test.go. Nothing here opens a network socket.

const sessionHeader = "vmware-api-session-id"

// recordedRequest is one entry of the mock's request log.
type recordedRequest struct {
	Method   string
	Path     string // URL path, still percent-encoded
	RawQuery string
	Header   http.Header
	Body     []byte
	Response []byte
	Status   int
	Known    bool // false when the request did not match a contract operation
}

// Session returns the session token presented on the request, and whether the
// header was present at all.
func (r recordedRequest) Session() (string, bool) {
	v, ok := r.Header[http.CanonicalHeaderKey(sessionHeader)]
	if !ok || len(v) == 0 {
		return "", false
	}
	return v[0], true
}

// IsSessionCreate reports whether the request targets Snapservice.Sessions_create.
func (r recordedRequest) IsSessionCreate() bool {
	return r.Method == http.MethodPost && r.Path == "/snapservice/sessions"
}

// IsSnapshotCreate reports whether the request targets
// Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task.
func (r recordedRequest) IsSnapshotCreate() bool {
	return r.Method == http.MethodPost && strings.HasSuffix(r.Path, "/snapshots")
}

// pgFixture is one protection group served by the mock.
type pgFixture struct {
	ID     string
	Name   string
	TaskID string // optional non-default task identifier, used by path-encoding tests
	Fail   bool   // the snapshot task for this protection group ends FAILED
}

// mockConfig configures one appliance instance.
type mockConfig struct {
	// bootstrap is the only token Snapservice.Sessions_create accepts.
	bootstrap string
	// clusters maps a cluster identifier to its protection groups, in the order
	// the list operation returns them.
	clusters map[string][]pgFixture
	// expireAfter invalidates every working session token once this many
	// authenticated non-session requests have been served. It fires at most
	// once. Zero disables expiry.
	expireAfter int
	// rejectAll answers every non-session request with 401 regardless of token.
	rejectAll bool
	// pollsToFinish is how many successful polls a task needs before it reports
	// a terminal status. Must be at least 1.
	pollsToFinish int
	// simultaneousUnauthorized holds 401 responses until this many requests
	// using the expired token have arrived. It makes the concurrent-refresh
	// scenario deterministic instead of depending on goroutine scheduling.
	simultaneousUnauthorized int
	// malformedResponse replaces one successful operation response with an
	// object that does not match that operation's response schema. Supported
	// values are "session", "list", "create", and "task".
	malformedResponse string
}

type mockTask struct {
	cluster string
	pg      string
	fail    bool
	polls   int
}

type mockAppliance struct {
	cfg        mockConfig
	httpClient *http.Client

	mu          sync.Mutex
	log         []recordedRequest
	live        map[string]bool
	minted      []string
	authed      int
	expiredOnce bool
	tasks       map[string]*mockTask
	taskSeq     int
	creates     map[string]int
	unknown     int

	unauthorizedMu      sync.Mutex
	unauthorizedArrived int
	unauthorizedRelease chan struct{}
}

func newMockAppliance(t *testing.T, cfg mockConfig) *mockAppliance {
	t.Helper()
	if cfg.bootstrap == "" {
		cfg.bootstrap = "bootstrap-token"
	}
	if cfg.pollsToFinish < 1 {
		cfg.pollsToFinish = 1
	}
	m := &mockAppliance{
		cfg:     cfg,
		live:    map[string]bool{},
		tasks:   map[string]*mockTask{},
		creates: map[string]int{},
	}
	if cfg.simultaneousUnauthorized > 0 {
		m.unauthorizedRelease = make(chan struct{})
	}
	m.httpClient = &http.Client{Transport: mockTransport{appliance: m}}
	return m
}

func (m *mockAppliance) baseURL() string { return "http://snapshot-appliance.test" }

type mockTransport struct {
	appliance *mockAppliance
}

func (tr mockTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	recorder := httptest.NewRecorder()
	tr.appliance.ServeHTTP(recorder, r)
	return recorder.Result(), nil
}

func (m *mockAppliance) requests() []recordedRequest {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]recordedRequest, len(m.log))
	copy(out, m.log)
	return out
}

// mintedTokens returns the working tokens handed out, oldest first.
func (m *mockAppliance) mintedTokens() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]string, len(m.minted))
	copy(out, m.minted)
	return out
}

func (m *mockAppliance) sessionCreates() int { return len(m.mintedTokens()) }

// acceptedCreates is the number of snapshot create requests the appliance
// actually acted on for a protection group. Requests rejected with 401 never
// reach this counter, so it is the true count of side effects.
func (m *mockAppliance) acceptedCreates(cluster, pg string) int {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.creates[cluster+"|"+pg]
}

func (m *mockAppliance) unknownRoutes() int {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.unknown
}

func (m *mockAppliance) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	var body []byte
	if r.Body != nil {
		body, _ = io.ReadAll(r.Body)
	}

	m.mu.Lock()
	status, payload, known := m.route(r, body)
	if status >= 200 && status < 300 && m.shouldMalformed(r) {
		payload = []byte(`{}`)
	}
	m.log = append(m.log, recordedRequest{
		Method:   r.Method,
		Path:     r.URL.EscapedPath(),
		RawQuery: r.URL.RawQuery,
		Header:   r.Header.Clone(),
		Body:     body,
		Response: append([]byte(nil), payload...),
		Status:   status,
		Known:    known,
	})
	if !known {
		m.unknown++
	}
	m.mu.Unlock()

	if status == http.StatusUnauthorized && m.cfg.simultaneousUnauthorized > 0 &&
		!(r.Method == http.MethodPost && r.URL.EscapedPath() == "/snapservice/sessions") {
		m.waitForUnauthorizedPeers()
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if len(payload) > 0 {
		_, _ = w.Write(payload)
	}
}

func (m *mockAppliance) shouldMalformed(r *http.Request) bool {
	path := r.URL.EscapedPath()
	switch m.cfg.malformedResponse {
	case "session":
		return r.Method == http.MethodPost && path == "/snapservice/sessions"
	case "list":
		return r.Method == http.MethodGet && strings.Contains(path, "/protection-groups")
	case "create":
		return r.Method == http.MethodPost && strings.HasSuffix(path, "/snapshots")
	case "task":
		return r.Method == http.MethodGet && strings.HasPrefix(path, "/snapservice/tasks/")
	default:
		return false
	}
}

func (m *mockAppliance) waitForUnauthorizedPeers() {
	m.unauthorizedMu.Lock()
	m.unauthorizedArrived++
	if m.unauthorizedArrived == m.cfg.simultaneousUnauthorized {
		close(m.unauthorizedRelease)
	}
	release := m.unauthorizedRelease
	m.unauthorizedMu.Unlock()
	<-release
}

// route dispatches one request. m.mu is held for the whole call.
func (m *mockAppliance) route(r *http.Request, body []byte) (int, []byte, bool) {
	segs := splitPath(r.URL.EscapedPath())

	switch {
	case len(segs) == 2 && segs[0] == "snapservice" && segs[1] == "sessions" &&
		r.Method == http.MethodPost:
		return m.sessionsCreate(r, body)

	case len(segs) == 4 && segs[0] == "snapservice" && segs[1] == "clusters" &&
		segs[3] == "protection-groups" && r.Method == http.MethodGet:
		if code, payload, ok := m.authorize(r); !ok {
			return code, payload, true
		}
		return m.protectionGroupsList(r, segs[2])

	case len(segs) == 6 && segs[0] == "snapservice" && segs[1] == "clusters" &&
		segs[3] == "protection-groups" && segs[5] == "snapshots" &&
		r.Method == http.MethodPost:
		if code, payload, ok := m.authorize(r); !ok {
			return code, payload, true
		}
		return m.snapshotsCreate(r, segs[2], segs[4], body)

	case len(segs) == 3 && segs[0] == "snapservice" && segs[1] == "tasks" &&
		r.Method == http.MethodGet:
		if code, payload, ok := m.authorize(r); !ok {
			return code, payload, true
		}
		return m.tasksGet(segs[2])
	}

	return http.StatusNotFound, jsonErr("NOT_FOUND", "no such operation in the pinned contract"), false
}

// Snapservice.Sessions_create: exchanges the long-lived bootstrap token for a
// short-lived working session token.
func (m *mockAppliance) sessionsCreate(r *http.Request, body []byte) (int, []byte, bool) {
	if r.URL.RawQuery != "" {
		return http.StatusBadRequest, jsonErr("INVALID_ARGUMENT", "unexpected query parameters"), true
	}
	if len(body) != 0 {
		return http.StatusBadRequest, jsonErr("INVALID_ARGUMENT", "unexpected request body"), true
	}
	if r.Header.Get(sessionHeader) != m.cfg.bootstrap {
		return http.StatusUnauthorized, unauthenticated(), true
	}
	tok := fmt.Sprintf("session-token-%d", len(m.minted)+1)
	m.minted = append(m.minted, tok)
	// Minting does not revoke tokens handed out earlier; only expiry does.
	m.live[tok] = true
	payload, _ := json.Marshal(tok)
	return http.StatusCreated, payload, true
}

// authorize applies the api_key_auth security scheme to every non-session
// operation and drives the single token-expiry event.
func (m *mockAppliance) authorize(r *http.Request) (int, []byte, bool) {
	if m.cfg.rejectAll {
		return http.StatusUnauthorized, unauthenticated(), false
	}
	tok := r.Header.Get(sessionHeader)
	if tok == "" || !m.live[tok] {
		return http.StatusUnauthorized, unauthenticated(), false
	}
	m.authed++
	if !m.expiredOnce && m.cfg.expireAfter > 0 && m.authed >= m.cfg.expireAfter {
		m.expiredOnce = true
		m.live = map[string]bool{}
	}
	return 0, nil, true
}

// Snapservice.Clusters.ProtectionGroups_list
func (m *mockAppliance) protectionGroupsList(r *http.Request, rawCluster string) (int, []byte, bool) {
	cluster, err := url.PathUnescape(rawCluster)
	if err != nil {
		return http.StatusBadRequest, jsonErr("INVALID_ARGUMENT", "undecodable cluster"), true
	}
	query, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil {
		return http.StatusBadRequest, jsonErr("INVALID_ARGUMENT", "undecodable query"), true
	}
	for key := range query {
		if key != "names" {
			return http.StatusBadRequest, jsonErr("INVALID_ARGUMENT",
				"unsupported query parameter "+key), true
		}
	}
	names := query["names"]
	seen := map[string]bool{}
	for _, n := range names {
		if n == "" {
			return http.StatusBadRequest, jsonErr("INVALID_ARGUMENT", "empty names value"), true
		}
		if seen[n] {
			return http.StatusBadRequest, jsonErr("INVALID_ARGUMENT", "names must be unique"), true
		}
		seen[n] = true
	}

	fixtures, ok := m.cfg.clusters[cluster]
	if !ok {
		return http.StatusNotFound, jsonErr("NOT_FOUND", "no such cluster "+cluster), true
	}

	items := []map[string]any{}
	for _, f := range fixtures {
		if len(names) > 0 && !seen[f.Name] {
			continue
		}
		items = append(items, map[string]any{
			"pg": f.ID,
			"info": map[string]any{
				"name":      f.Name,
				"status":    "PROTECTED",
				"vms":       []string{f.ID + "-vm-1"},
				"snapshots": []string{},
				"locked":    false,
			},
		})
	}
	payload, _ := json.Marshal(map[string]any{"items": items})
	return http.StatusOK, payload, true
}

// Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task
func (m *mockAppliance) snapshotsCreate(r *http.Request, rawCluster, rawPG string, body []byte) (int, []byte, bool) {
	cluster, err1 := url.PathUnescape(rawCluster)
	pg, err2 := url.PathUnescape(rawPG)
	if err1 != nil || err2 != nil {
		return http.StatusBadRequest, jsonErr("INVALID_ARGUMENT", "undecodable path segment"), true
	}
	query, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil || len(query) != 1 || len(query["vmw-task"]) != 1 || query.Get("vmw-task") != "true" {
		return http.StatusBadRequest, jsonErr("INVALID_ARGUMENT",
			"this operation is only exposed as a task; expected exactly vmw-task=true"), true
	}
	if ct := r.Header.Get("Content-Type"); ct != "application/json" {
		return http.StatusUnsupportedMediaType, jsonErr("INVALID_ARGUMENT",
			"expected Content-Type application/json, got "+ct), true
	}

	var spec map[string]json.RawMessage
	if err := json.Unmarshal(body, &spec); err != nil {
		return http.StatusBadRequest, jsonErr("INVALID_ARGUMENT", "undecodable body"), true
	}
	for key := range spec {
		if key != "name" && key != "retention" {
			return http.StatusBadRequest, jsonErr("INVALID_ARGUMENT",
				"CreateSpec has no property "+key), true
		}
	}
	var name string
	if err := json.Unmarshal(spec["name"], &name); err != nil || name == "" {
		return http.StatusBadRequest, jsonErr("INVALID_ARGUMENT", "name is required"), true
	}
	if raw, present := spec["retention"]; present {
		// The schema marks both retention properties required, so a retention
		// that is null, empty, or partial is a malformed request rather than
		// "unset". Unset means the property is absent.
		var retention map[string]json.RawMessage
		if err := json.Unmarshal(raw, &retention); err != nil || retention == nil {
			return http.StatusBadRequest, jsonErr("INVALID_ARGUMENT",
				"retention must be omitted rather than sent empty"), true
		}
		if len(retention) != 2 {
			return http.StatusBadRequest, jsonErr("INVALID_ARGUMENT",
				"retention requires exactly unit and duration"), true
		}
		var unit string
		var duration int64
		if err := json.Unmarshal(retention["unit"], &unit); err != nil || unit == "" {
			return http.StatusBadRequest, jsonErr("INVALID_ARGUMENT", "retention.unit is required"), true
		}
		if err := json.Unmarshal(retention["duration"], &duration); err != nil || duration <= 0 {
			return http.StatusBadRequest, jsonErr("INVALID_ARGUMENT", "retention.duration is required"), true
		}
	}

	var fixture *pgFixture
	for i, f := range m.cfg.clusters[cluster] {
		if f.ID == pg {
			fixture = &m.cfg.clusters[cluster][i]
			break
		}
	}
	if fixture == nil {
		return http.StatusNotFound, jsonErr("NOT_FOUND", "no such protection group "+pg), true
	}

	m.creates[cluster+"|"+pg]++
	m.taskSeq++
	id := fixture.TaskID
	if id == "" {
		id = fmt.Sprintf("task-%d", m.taskSeq)
	}
	m.tasks[id] = &mockTask{cluster: cluster, pg: pg, fail: fixture.Fail}
	payload, _ := json.Marshal(id)
	return http.StatusAccepted, payload, true
}

// Snapservice.Tasks_get
func (m *mockAppliance) tasksGet(rawTask string) (int, []byte, bool) {
	id, err := url.PathUnescape(rawTask)
	if err != nil {
		return http.StatusBadRequest, jsonErr("INVALID_ARGUMENT", "undecodable task"), true
	}
	task, ok := m.tasks[id]
	if !ok {
		return http.StatusNotFound, jsonErr("NOT_FOUND", "no such task "+id), true
	}
	task.polls++

	info := map[string]any{
		"cancelable": false,
		"service":    "com.vmware.snapservice.clusters.protection_groups.snapshots",
		"operation":  "create$task",
		"description": map[string]any{
			"id":              "com.vmware.snapservice.snapshot.create",
			"default_message": "Create a protection group snapshot",
			"args":            []string{},
		},
	}
	switch {
	case task.polls < m.cfg.pollsToFinish:
		info["status"] = "RUNNING"
		info["progress"] = map[string]any{
			"total":     100,
			"completed": 50,
			"message": map[string]any{
				"id":              "com.vmware.snapservice.snapshot.progress",
				"default_message": "Quiescing virtual machines",
				"args":            []string{},
			},
		}
	case task.fail:
		info["status"] = "FAILED"
		info["error"] = map[string]any{
			"error_type": "ERROR",
			"messages": []map[string]any{{
				"id":              "com.vmware.snapservice.snapshot.failed",
				"default_message": "Snapshot creation failed on " + task.pg,
				"args":            []string{},
			}},
		}
	default:
		info["status"] = "SUCCEEDED"
		info["result"] = "snap-" + id
	}
	payload, _ := json.Marshal(info)
	return http.StatusOK, payload, true
}

func splitPath(escaped string) []string {
	trimmed := strings.Trim(escaped, "/")
	if trimmed == "" {
		return nil
	}
	return strings.Split(trimmed, "/")
}

func unauthenticated() []byte {
	payload, _ := json.Marshal(map[string]any{
		"error_type": "UNAUTHENTICATED",
		"messages": []map[string]any{{
			"id":              "com.vmware.vapi.std.errors.unauthenticated",
			"default_message": "The session token is missing or has expired.",
			"args":            []string{},
		}},
		"challenge": sessionHeader,
	})
	return payload
}

func jsonErr(kind, message string) []byte {
	payload, _ := json.Marshal(map[string]any{
		"error_type": kind,
		"messages": []map[string]any{{
			"id":              "com.vmware.snapservice.mock",
			"default_message": message,
			"args":            []string{},
		}},
	})
	return payload
}
