// Package mockappliance runs a loopback stand-in for the VMware Cloud
// Foundation 9.1 vSAN Data Protection Snapshot Appliance.
//
// The appliance is pinned to docs/contract.json: its routing table is built
// from the operations that contract names, and it refuses to serve anything
// else. Every request that arrives, matched or not, is appended to a request
// log the tests read back.
//
// This file is protected.
package mockappliance

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"testing"
)

// Contract is the subset of docs/contract.json the appliance needs.
type Contract struct {
	ContractVersion int `json:"contract_version"`
	Source          struct {
		Repository string `json:"repository"`
		License    string `json:"license"`
		CommitSHA  string `json:"commit_sha"`
		SpecPath   string `json:"spec_path"`
		OpenAPI    string `json:"openapi"`
	} `json:"source"`
	SpecTitle   string `json:"spec_title"`
	SpecVersion string `json:"spec_version"`
	BasePath    string `json:"base_path"`
	Auth        struct {
		Scheme string `json:"scheme"`
		Type   string `json:"type"`
		In     string `json:"in"`
		Name   string `json:"name"`
	} `json:"auth"`
	TaskStatus struct {
		Schema      string   `json:"schema"`
		Values      []string `json:"values"`
		NonTerminal []string `json:"non_terminal"`
		Terminal    []string `json:"terminal"`
		Success     string   `json:"success"`
		Failure     string   `json:"failure"`
	} `json:"task_status"`
	Operations []Operation `json:"operations"`
}

// Operation is one contract-named operation.
type Operation struct {
	OperationID   string            `json:"operation_id"`
	Tag           string            `json:"tag"`
	Asynchronous  bool              `json:"asynchronous"`
	Method        string            `json:"method"`
	Path          string            `json:"path"`
	PathParams    []string          `json:"path_params"`
	Query         map[string]string `json:"query"`
	RequestBody   *RequestBody      `json:"request_body"`
	SuccessStatus int               `json:"success_status"`
	SuccessBody   struct {
		Type           string   `json:"type"`
		Schema         string   `json:"schema"`
		Meaning        string   `json:"meaning"`
		RequiredFields []string `json:"required_fields"`
		OptionalFields []string `json:"optional_fields"`
	} `json:"success_body"`
	ErrorResponses map[string]string `json:"error_responses"`
}

// RequestBody describes a contract-named request body.
type RequestBody struct {
	Required       bool                   `json:"required"`
	ContentType    string                 `json:"content_type"`
	Schema         string                 `json:"schema"`
	RequiredFields []string               `json:"required_fields"`
	OptionalFields []string               `json:"optional_fields"`
	FieldSchemas   map[string]FieldSchema `json:"field_schemas"`
}

// FieldSchema describes one request body property.
type FieldSchema struct {
	Type           string                 `json:"type"`
	Format         string                 `json:"format"`
	Schema         string                 `json:"schema"`
	Enum           []string               `json:"enum"`
	RequiredFields []string               `json:"required_fields"`
	OptionalFields []string               `json:"optional_fields"`
	FieldSchemas   map[string]FieldSchema `json:"field_schemas"`
}

// LoadContract reads and validates a contract document.
func LoadContract(path string) (*Contract, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("mockappliance: read contract: %w", err)
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	var c Contract
	if err := dec.Decode(&c); err != nil {
		return nil, fmt.Errorf("mockappliance: parse contract: %w", err)
	}
	if c.BasePath == "" {
		return nil, fmt.Errorf("mockappliance: contract declares no base_path")
	}
	if c.Auth.Name == "" || !strings.EqualFold(c.Auth.In, "header") {
		return nil, fmt.Errorf("mockappliance: contract declares no header auth scheme")
	}
	if len(c.Operations) == 0 {
		return nil, fmt.Errorf("mockappliance: contract names no operations")
	}
	return &c, nil
}

// TaskScript is the sequence of Snapservice.Tasks.Status values one task walks
// through. The final entry repeats if it is polled again.
type TaskScript struct {
	// ID is the task identifier handed back in the 202 body.
	ID string
	// States are the successive statuses reported by Snapservice.Tasks_get.
	States []string
	// Result is attached to the terminal Snapservice.Tasks.Info as its result
	// property when the task succeeds.
	Result any
	// FailureMessage is the appliance-side localizable message attached to a
	// failed task. It must never reach a client-produced error string.
	FailureMessage string
	// OmitTerminalTimes leaves the optional start_time and end_time properties
	// out of a successful terminal response.
	OmitTerminalTimes bool
}

// Config configures one appliance.
type Config struct {
	// ContractPath points at docs/contract.json.
	ContractPath string
	// SessionID is the token the appliance accepts in the contract's auth
	// header. Any other value is answered with 401.
	SessionID string
	// Tasks maps a CreateSpec name to the task the create operation starts.
	Tasks map[string]TaskScript
	// CreateStatus, when non-zero, replaces the create operation's normal
	// response with that status and an error body.
	CreateStatus int
	// CreateBodyMarker is placed in the create operation's error body. It must
	// never reach a client-produced error string.
	CreateBodyMarker string
	// CreateTaskIDOverride, when non-empty, is the identifier the create
	// operation hands back instead of the scripted one, so that the following
	// Snapservice.Tasks_get finds no such task.
	CreateTaskIDOverride string
	// CreateRawBody, when non-nil, replaces the normal 202 JSON task identifier
	// body. It is used to exercise unusable success responses.
	CreateRawBody []byte
	// TaskRawBody, when non-nil, replaces the normal 200 task info body. It is
	// used to exercise unusable success responses.
	TaskRawBody []byte
	// OnPoll, when set, is called after each Snapservice.Tasks_get response is
	// written, with the 1-based poll count for that task.
	OnPoll func(taskID string, poll int)
}

// Request is one logged inbound request.
type Request struct {
	// OperationID is the contract operation that matched, or "" when nothing
	// in the contract matched.
	OperationID string
	Matched     bool
	Method      string
	// Path is the decoded request path, RawPath is the path as it arrived on
	// the wire.
	Path       string
	RawPath    string
	RawQuery   string
	Query      url.Values
	Header     http.Header
	Body       []byte
	PathParams map[string]string
}

type route struct {
	op      Operation
	re      *regexp.Regexp
	names   []string
	handler func(*Appliance, http.ResponseWriter, *Request)
}

// handlers is the set of operations this appliance knows how to serve. The
// contract decides which of them are actually routed.
var handlers = map[string]func(*Appliance, http.ResponseWriter, *Request){
	"Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task": handleCreateSnapshot,
	"Snapservice.Tasks_get": handleTaskGet,
}

// Appliance is a running loopback appliance.
type Appliance struct {
	contract *Contract
	cfg      Config
	routes   []route
	srv      *httptest.Server

	mu     sync.Mutex
	log    []Request
	polls  map[string]int
	byTask map[string]TaskScript
}

// New starts an appliance on an ephemeral 127.0.0.1 port and stops it when the
// test finishes.
func New(t testing.TB, cfg Config) *Appliance {
	t.Helper()
	contract, err := LoadContract(cfg.ContractPath)
	if err != nil {
		t.Fatalf("%v", err)
	}
	a := &Appliance{
		contract: contract,
		cfg:      cfg,
		polls:    map[string]int{},
		byTask:   map[string]TaskScript{},
	}
	for name, script := range cfg.Tasks {
		if script.ID == "" {
			t.Fatalf("mockappliance: task script %q has no identifier", name)
		}
		if _, dup := a.byTask[script.ID]; dup {
			t.Fatalf("mockappliance: task identifier %q is used twice", script.ID)
		}
		a.byTask[script.ID] = script
	}

	served := map[string]bool{}
	for _, op := range contract.Operations {
		h, ok := handlers[op.OperationID]
		if !ok {
			t.Fatalf("mockappliance: contract names operationId %q, which this appliance does not serve", op.OperationID)
		}
		if served[op.OperationID] {
			t.Fatalf("mockappliance: contract names operationId %q twice", op.OperationID)
		}
		served[op.OperationID] = true
		re, names, err := compilePath(contract.BasePath, op)
		if err != nil {
			t.Fatalf("%v", err)
		}
		a.routes = append(a.routes, route{op: op, re: re, names: names, handler: h})
	}
	for id := range handlers {
		if !served[id] {
			t.Fatalf("mockappliance: contract does not name operationId %q", id)
		}
	}

	a.srv = httptest.NewServer(http.HandlerFunc(a.serve))
	t.Cleanup(a.srv.Close)
	return a
}

func compilePath(basePath string, op Operation) (*regexp.Regexp, []string, error) {
	declared := map[string]bool{}
	for _, p := range op.PathParams {
		declared[p] = true
	}
	var (
		names   []string
		pattern strings.Builder
	)
	pattern.WriteString("^" + regexp.QuoteMeta(strings.TrimSuffix(basePath, "/")))
	rest := op.Path
	for {
		open := strings.Index(rest, "{")
		if open < 0 {
			pattern.WriteString(regexp.QuoteMeta(rest))
			break
		}
		closing := strings.Index(rest[open:], "}")
		if closing < 0 {
			return nil, nil, fmt.Errorf("mockappliance: operationId %q has an unbalanced path template %q", op.OperationID, op.Path)
		}
		name := rest[open+1 : open+closing]
		if !declared[name] {
			return nil, nil, fmt.Errorf("mockappliance: operationId %q uses path parameter %q that path_params does not declare", op.OperationID, name)
		}
		pattern.WriteString(regexp.QuoteMeta(rest[:open]))
		pattern.WriteString("([^/]+)")
		names = append(names, name)
		rest = rest[open+closing+1:]
	}
	pattern.WriteString("$")
	if len(names) != len(op.PathParams) {
		return nil, nil, fmt.Errorf("mockappliance: operationId %q declares %d path parameters but its template uses %d", op.OperationID, len(op.PathParams), len(names))
	}
	re, err := regexp.Compile(pattern.String())
	if err != nil {
		return nil, nil, fmt.Errorf("mockappliance: operationId %q has an uncompilable path template: %w", op.OperationID, err)
	}
	return re, names, nil
}

// URL is the appliance service root, with no base path attached.
func (a *Appliance) URL() string { return a.srv.URL }

// Contract is the contract the appliance was pinned to.
func (a *Appliance) Contract() *Contract { return a.contract }

// Requests returns a copy of the request log.
func (a *Appliance) Requests() []Request {
	a.mu.Lock()
	defer a.mu.Unlock()
	out := make([]Request, len(a.log))
	copy(out, a.log)
	return out
}

// RequestsFor returns the logged requests that matched one operationId.
func (a *Appliance) RequestsFor(operationID string) []Request {
	var out []Request
	for _, r := range a.Requests() {
		if r.Matched && r.OperationID == operationID {
			out = append(out, r)
		}
	}
	return out
}

func (a *Appliance) serve(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	logged := Request{
		Method:   r.Method,
		Path:     r.URL.Path,
		RawPath:  r.URL.EscapedPath(),
		RawQuery: r.URL.RawQuery,
		Query:    r.URL.Query(),
		Header:   r.Header.Clone(),
		Body:     body,
	}

	var matched *route
	for i := range a.routes {
		rt := &a.routes[i]
		if !strings.EqualFold(rt.op.Method, r.Method) {
			continue
		}
		// Match the escaped form so a correctly escaped slash remains inside one
		// path segment. net/http exposes the decoded form in URL.Path.
		m := rt.re.FindStringSubmatch(r.URL.EscapedPath())
		if m == nil {
			continue
		}
		if !queryMatches(rt.op.Query, logged.Query) {
			continue
		}
		params := map[string]string{}
		for i, name := range rt.names {
			params[name], _ = url.PathUnescape(m[i+1])
		}
		logged.Matched = true
		logged.OperationID = rt.op.OperationID
		logged.PathParams = params
		matched = rt
		break
	}

	a.mu.Lock()
	a.log = append(a.log, logged)
	a.mu.Unlock()

	if matched == nil {
		writeError(w, http.StatusNotFound, "Vapi.Std.Errors.NotFound",
			"com.vmware.snapservice.no_such_operation", "The appliance serves no such operation.")
		return
	}
	if r.Header.Get(a.contract.Auth.Name) != a.cfg.SessionID {
		writeError(w, http.StatusUnauthorized, "Vapi.Std.Errors.Unauthenticated",
			"com.vmware.snapservice.unauthenticated", "The session token is missing or not valid.")
		return
	}
	matched.handler(a, w, &logged)
}

// errorSchema is the error schema the contract declares for one operation and
// status, so that error bodies stay faithful to the pinned specification.
func (a *Appliance) errorSchema(operationID string, status int) string {
	for _, op := range a.contract.Operations {
		if op.OperationID == operationID {
			return op.ErrorResponses[strconv.Itoa(status)]
		}
	}
	return ""
}

func queryMatches(declared map[string]string, got url.Values) bool {
	for k, v := range declared {
		if got.Get(k) != v {
			return false
		}
	}
	return true
}

func handleCreateSnapshot(a *Appliance, w http.ResponseWriter, r *Request) {
	if a.cfg.CreateStatus != 0 {
		marker := a.cfg.CreateBodyMarker
		if marker == "" {
			marker = "appliance rejected the snapshot"
		}
		writeError(w, a.cfg.CreateStatus, a.errorSchema(r.OperationID, a.cfg.CreateStatus),
			"com.vmware.snapservice.create_rejected", marker)
		return
	}
	if a.cfg.CreateRawBody != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write(a.cfg.CreateRawBody)
		return
	}
	var spec map[string]any
	if err := json.Unmarshal(r.Body, &spec); err != nil {
		writeError(w, http.StatusBadRequest, "Vapi.Std.Errors.InvalidArgument",
			"com.vmware.snapservice.malformed_spec", "The request body is not a JSON object.")
		return
	}
	name, _ := spec["name"].(string)
	script, ok := a.cfg.Tasks[name]
	if !ok {
		writeError(w, http.StatusBadRequest, "Vapi.Std.Errors.InvalidArgument",
			"com.vmware.snapservice.unknown_snapshot_name", "No snapshot is scripted under that name.")
		return
	}
	id := script.ID
	if a.cfg.CreateTaskIDOverride != "" {
		id = a.cfg.CreateTaskIDOverride
	}
	writeJSON(w, http.StatusAccepted, id)
}

func handleTaskGet(a *Appliance, w http.ResponseWriter, r *Request) {
	taskID := r.PathParams["task"]
	script, ok := a.byTask[taskID]
	if !ok {
		writeError(w, http.StatusNotFound, "Vapi.Std.Errors.NotFound",
			"com.vmware.snapservice.no_such_task", "No task has that identifier.")
		return
	}

	a.mu.Lock()
	a.polls[taskID]++
	n := a.polls[taskID]
	a.mu.Unlock()

	idx := n - 1
	if idx >= len(script.States) {
		idx = len(script.States) - 1
	}
	status := script.States[idx]
	if a.cfg.TaskRawBody != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write(a.cfg.TaskRawBody)
		if a.cfg.OnPoll != nil {
			a.cfg.OnPoll(taskID, n)
		}
		return
	}

	info := map[string]any{
		"cancelable": false,
		"description": map[string]any{
			"id":              "com.vmware.snapservice.create_pg_snapshot",
			"default_message": "Create a protection group snapshot.",
			"args":            []any{},
		},
		"service":   "com.vmware.snapservice.clusters.protection_groups.snapshots",
		"operation": "create",
		"status":    status,
	}
	switch status {
	case "PENDING":
	case "SUCCEEDED":
		if !script.OmitTerminalTimes {
			info["start_time"] = "2026-05-13T08:19:58.000Z"
			info["end_time"] = "2026-05-13T08:20:41.000Z"
		}
		info["progress"] = map[string]any{"total": 100, "completed": 100, "message": map[string]any{
			"id": "com.vmware.snapservice.progress", "default_message": "Completed.", "args": []any{}}}
		if script.Result != nil {
			info["result"] = script.Result
		}
	case "FAILED":
		info["start_time"] = "2026-05-13T08:19:58.000Z"
		info["end_time"] = "2026-05-13T08:20:11.000Z"
		msg := script.FailureMessage
		if msg == "" {
			msg = "The snapshot operation failed."
		}
		info["error"] = map[string]any{
			"error_type": "ERROR",
			"messages": []any{map[string]any{
				"id": "com.vmware.snapservice.snapshot_failed", "default_message": msg, "args": []any{}}},
		}
	default:
		info["start_time"] = "2026-05-13T08:19:58.000Z"
		info["progress"] = map[string]any{"total": 100, "completed": 40, "message": map[string]any{
			"id": "com.vmware.snapservice.progress", "default_message": "Working.", "args": []any{}}}
	}

	writeJSON(w, http.StatusOK, info)
	if a.cfg.OnPoll != nil {
		a.cfg.OnPoll(taskID, n)
	}
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	body, err := json.Marshal(payload)
	if err != nil {
		http.Error(w, "marshal failure", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

func writeError(w http.ResponseWriter, status int, schema, messageID, message string) {
	if schema == "" {
		schema = "Vapi.Std.Errors.Error"
	}
	writeJSON(w, status, map[string]any{
		"error_type": strings.ToUpper(strings.TrimPrefix(schema, "Vapi.Std.Errors.")),
		"messages": []any{map[string]any{
			"id":              messageID,
			"default_message": message,
			"args":            []any{},
		}},
	})
}
