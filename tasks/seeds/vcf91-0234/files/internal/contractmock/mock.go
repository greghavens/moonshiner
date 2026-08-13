// Package contractmock runs an in-process stand-in for the VCF 9.1 SDDC LCM
// service. Its routing table is built at startup from docs/contract.json, so it
// answers exactly the operations that contract names and nothing else. Every
// request it receives is appended to an in-memory log that tests can read.
//
// The mock never opens a network connection and no live VMware endpoint is
// involved.
package contractmock

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
)

// Fixed instants so responses are byte-stable across runs.
const (
	createTime = "2026-05-13T11:27:00.000Z"
	updateTime = "2026-05-13T11:29:30.000Z"
)

// Operation is one entry of the pinned contract's operation list.
type Operation struct {
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	Path        string `json:"path"`
}

type contractDoc struct {
	Source struct {
		Commit     string `json:"commit"`
		Path       string `json:"path"`
		APIVersion string `json:"api_version"`
	} `json:"source"`
	Operations []Operation `json:"operations"`
}

// Record is a single observed request, captured before any decoding so tests
// can assert the exact wire shape.
type Record struct {
	Method   string
	RawPath  string // percent-encoded path, exactly as it arrived
	RawQuery string
	Header   http.Header
	Body     []byte
}

// Options configures one mock instance.
type Options struct {
	// LostAcceptResponses makes the first N apply submissions record their
	// work as usual and then answer 500 instead of 202, simulating a
	// response that never made it back to the caller. A client that is safe
	// to retry re-sends the same submission and must not start a second
	// upgrade.
	LostAcceptResponses int

	// TaskStatuses is the status sequence handed out by successive getTask
	// calls for a task. The final entry repeats once exhausted. Defaults to
	// {"RUNNING", "SUCCEEDED"}.
	TaskStatuses []string
}

type submission struct {
	taskID string
	body   []byte
}

type taskState struct {
	id           string
	componentID  string
	correlation  string
	pollsServed  int
	statusesUsed []string
}

// Mock is a running loopback contract fixture.
type Mock struct {
	operations []Operation
	commit     string
	specPath   string

	mu          sync.Mutex
	requests    []Record
	submissions map[string]submission // componentID + "\x00" + correlationId
	tasks       map[string]*taskState
	createdIDs  []string
	lostBudget  int
	statuses    []string
}

// Start constructs an isolated in-process fixture for one test.
func Start(t *testing.T, opts Options) *Mock {
	t.Helper()

	doc, err := loadContract()
	if err != nil {
		t.Fatalf("contractmock: %v", err)
	}

	statuses := opts.TaskStatuses
	if len(statuses) == 0 {
		statuses = []string{"RUNNING", "SUCCEEDED"}
	}

	m := &Mock{
		operations:  doc.Operations,
		commit:      doc.Source.Commit,
		specPath:    doc.Source.Path,
		submissions: make(map[string]submission),
		tasks:       make(map[string]*taskState),
		lostBudget:  opts.LostAcceptResponses,
		statuses:    append([]string(nil), statuses...),
	}
	return m
}

// BaseURL is a syntactically valid, non-routable root. HTTPClient dispatches
// requests for it directly to the fixture without opening a socket.
func (m *Mock) BaseURL() string { return "http://contractmock.local" }

// HTTPClient returns a client whose transport invokes this fixture in process.
// The request still passes through net/http's real request and response types,
// but verification does not depend on DNS, an available port, or network
// permissions.
func (m *Mock) HTTPClient() *http.Client {
	return &http.Client{Transport: roundTripperFunc(func(req *http.Request) (*http.Response, error) {
		recorder := httptest.NewRecorder()
		m.serve(recorder, req)
		return recorder.Result(), nil
	})}
}

type roundTripperFunc func(*http.Request) (*http.Response, error)

func (f roundTripperFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

// ContractOperations returns the operations this fixture was pinned to.
func (m *Mock) ContractOperations() []Operation {
	return append([]Operation(nil), m.operations...)
}

// ContractSource returns the pinned spec commit and path.
func (m *Mock) ContractSource() (commit, specPath string) { return m.commit, m.specPath }

// Requests returns a copy of the request log in arrival order.
func (m *Mock) Requests() []Record {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]Record, len(m.requests))
	copy(out, m.requests)
	return out
}

// CreatedTaskIDs returns the ids of upgrades this fixture actually started, in
// creation order. A retry-safe client produces one entry per logical upgrade
// however many times it re-sends the submission.
func (m *Mock) CreatedTaskIDs() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	return append([]string(nil), m.createdIDs...)
}

func loadContract() (*contractDoc, error) {
	_, thisFile, _, ok := runtime.Caller(0)
	if !ok {
		return nil, fmt.Errorf("cannot locate contractmock source")
	}
	root := filepath.Dir(filepath.Dir(filepath.Dir(thisFile)))
	path := filepath.Join(root, "docs", "contract.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	doc := &contractDoc{}
	if err := json.Unmarshal(raw, doc); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	if len(doc.Operations) == 0 {
		return nil, fmt.Errorf("%s names no operations", path)
	}
	return doc, nil
}

// matchTemplate reports whether escapedPath matches an OpenAPI path template
// such as /v1/components/{componentId}, returning the captured values.
func matchTemplate(template, escapedPath string) (map[string]string, bool) {
	want := strings.Split(strings.Trim(template, "/"), "/")
	got := strings.Split(strings.Trim(escapedPath, "/"), "/")
	if len(want) != len(got) {
		return nil, false
	}
	captured := map[string]string{}
	for i, seg := range want {
		if strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}") {
			if got[i] == "" {
				return nil, false
			}
			captured[strings.Trim(seg, "{}")] = got[i]
			continue
		}
		if seg != got[i] {
			return nil, false
		}
	}
	return captured, true
}

func (m *Mock) serve(w http.ResponseWriter, r *http.Request) {
	body, _ := readAll(r)
	m.mu.Lock()
	m.requests = append(m.requests, Record{
		Method:   r.Method,
		RawPath:  r.URL.EscapedPath(),
		RawQuery: r.URL.RawQuery,
		Header:   r.Header.Clone(),
		Body:     body,
	})
	m.mu.Unlock()

	for _, op := range m.operations {
		params, ok := matchTemplate(op.Path, r.URL.EscapedPath())
		if !ok || op.Method != r.Method {
			continue
		}
		switch op.OperationID {
		case "performComponentAction":
			m.performComponentAction(w, r, params["componentId"], body)
		case "getTask":
			m.getTask(w, params["taskId"])
		default:
			writeError(w, http.StatusNotFound, "SDDC_LCM_OPERATION_NOT_IMPLEMENTED",
				fmt.Sprintf("operation %s is named by the contract but not served", op.OperationID))
		}
		return
	}

	writeError(w, http.StatusNotFound, "SDDC_LCM_UNKNOWN_OPERATION",
		fmt.Sprintf("%s %s is not one of the contract operations", r.Method, r.URL.EscapedPath()))
}

func (m *Mock) performComponentAction(w http.ResponseWriter, r *http.Request, componentID string, body []byte) {
	action := r.URL.Query().Get("action")
	if action == "" {
		writeError(w, http.StatusBadRequest, "SDDC_LCM_ACTION_REQUIRED",
			"the action query parameter is required")
		return
	}
	if action != "apply" {
		writeError(w, http.StatusBadRequest, "SDDC_LCM_UNSUPPORTED_ACTION",
			fmt.Sprintf("this fixture serves action=apply only, got %q", action))
		return
	}

	correlation := r.Header.Get("X-Correlation-Id")
	if correlation == "" {
		writeError(w, http.StatusBadRequest, "SDDC_LCM_CORRELATION_ID_REQUIRED",
			"a mutating apply must carry X-Correlation-Id so it can be replayed safely")
		return
	}

	var payload struct {
		ComponentSpec json.RawMessage `json:"componentSpec"`
		CorrelationID string          `json:"correlationId"`
	}
	if err := json.Unmarshal(body, &payload); err != nil {
		writeError(w, http.StatusBadRequest, "SDDC_LCM_MALFORMED_BODY",
			"request body is not a ComponentUpgradeSpec JSON object")
		return
	}
	if len(payload.ComponentSpec) == 0 {
		writeError(w, http.StatusBadRequest, "SDDC_LCM_COMPONENT_SPEC_REQUIRED",
			"componentSpec is required by ComponentUpgradeSpec")
		return
	}
	if payload.CorrelationID != correlation {
		writeError(w, http.StatusBadRequest, "SDDC_LCM_CORRELATION_ID_MISMATCH",
			"body correlationId must equal the X-Correlation-Id header")
		return
	}

	key := componentID + "\x00" + correlation

	m.mu.Lock()
	prior, replay := m.submissions[key]
	if replay && !bytes.Equal(prior.body, body) {
		m.mu.Unlock()
		writeError(w, http.StatusBadRequest, "SDDC_LCM_IDEMPOTENCY_CONFLICT",
			"correlationId was already used for a different ComponentUpgradeSpec")
		return
	}
	if !replay {
		id := fmt.Sprintf("2f8b0a1c-0000-4000-8000-%012d", len(m.createdIDs)+1)
		m.tasks[id] = &taskState{
			id:           id,
			componentID:  componentID,
			correlation:  correlation,
			statusesUsed: m.statuses,
		}
		m.createdIDs = append(m.createdIDs, id)
		prior = submission{taskID: id, body: append([]byte(nil), body...)}
		m.submissions[key] = prior
	}
	lost := m.lostBudget > 0
	if lost {
		m.lostBudget--
	}
	accepted := taskBody(m.tasks[prior.taskID], "PENDING")
	m.mu.Unlock()

	if lost {
		writeError(w, http.StatusInternalServerError, "SDDC_LCM_INTERNAL_ERROR",
			"the upgrade was accepted but the response was lost")
		return
	}
	if replay {
		w.Header().Set("X-Idempotent-Replay", "true")
	}
	writeJSON(w, http.StatusAccepted, accepted)
}

func (m *Mock) getTask(w http.ResponseWriter, taskID string) {
	m.mu.Lock()
	state, ok := m.tasks[taskID]
	if !ok {
		m.mu.Unlock()
		writeError(w, http.StatusNotFound, "SDDC_LCM_TASK_NOT_FOUND",
			fmt.Sprintf("no task with id %q", taskID))
		return
	}
	idx := state.pollsServed
	if idx >= len(state.statusesUsed) {
		idx = len(state.statusesUsed) - 1
	}
	status := state.statusesUsed[idx]
	state.pollsServed++
	body := taskBody(state, status)
	m.mu.Unlock()

	writeJSON(w, http.StatusOK, body)
}

func taskBody(state *taskState, status string) map[string]any {
	return map[string]any{
		"id":     state.id,
		"name":   "sddc_component_apply",
		"type":   "apply",
		"status": status,
		"description": map[string]any{
			"id":               "com.broadcom.lcm.ops.component.upgrade.started",
			"defaultMessage":   "Started upgrade for component",
			"localizedMessage": "Started upgrade for component",
			"args":             map[string]string{"componentId": state.componentID},
		},
		"createdBy":     "svc-sddc-lcm",
		"resourceId":    state.componentID,
		"resourceType":  "COMPONENT",
		"correlationId": state.correlation,
		"createTime":    createTime,
		"startTime":     createTime,
		"updateTime":    updateTime,
		"retriable":     status == "FAILED",
		"cancellable":   status == "RUNNING" || status == "PENDING",
	}
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	raw, err := json.Marshal(value)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(raw)
}

func writeError(w http.ResponseWriter, status int, code, message string) {
	writeJSON(w, status, map[string]any{
		"code":        code,
		"message":     map[string]any{"id": code, "defaultMessage": message, "localizedMessage": message},
		"resolution":  map[string]any{"id": code + ".resolution", "defaultMessage": "See the SDDC LCM service log.", "localizedMessage": "See the SDDC LCM service log."},
		"referenceId": "ref-" + strings.ToLower(code),
		"timestamp":   updateTime,
	})
}

func readAll(r *http.Request) ([]byte, error) {
	if r.Body == nil {
		return nil, nil
	}
	defer func() { _ = r.Body.Close() }()
	buf := &bytes.Buffer{}
	if _, err := buf.ReadFrom(r.Body); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}
