package mocklcm

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sort"
	"strings"
	"sync"
	"time"
)

// Component is one row of the inventory returned by getComponents.
type Component struct {
	ID            string `json:"id"`
	ComponentType string `json:"componentType"`
	Version       string `json:"version"`
	Scope         string `json:"scope"`
	Fqdn          string `json:"fqdn,omitempty"`
}

// Backup is one row of the catalogue returned by getComponentsBackups. At is
// the RFC 3339 instant the backup was taken; it is what periodStart and
// periodEnd filter on and is not part of the response body.
type Backup struct {
	Name             string   `json:"name"`
	Path             string   `json:"path"`
	Points           []string `json:"points,omitempty"`
	ComponentType    string   `json:"componentType"`
	ComponentID      string   `json:"componentId"`
	ComponentVersion string   `json:"componentVersion"`
	At               string   `json:"-"`
}

// Message is a localizable message carried by a task.
type Message struct {
	ID             string `json:"id"`
	DefaultMessage string `json:"defaultMessage"`
}

// TaskOutcome describes how the mock finishes the task raised for a component.
// Status is the terminal TaskStatus. When FailedStage is set the mock marks the
// stage of that name FAILED and hangs Errors off it as ERROR level stage
// messages; otherwise Errors are emitted as task level ERROR messages.
type TaskOutcome struct {
	Status      string
	FailedStage string
	Errors      []Message
}

// Options configures one mock instance.
type Options struct {
	// ContractPath is the docs/contract.json the routes are built from.
	ContractPath string
	// Token is the bearer credential the mock demands on every request.
	Token string
	// Components is the inventory getComponents serves.
	Components []Component
	// Backups is the catalogue getComponentsBackups serves.
	Backups []Backup
	// Restores maps a component id to the outcome of its restore task.
	// A component absent from the map gets a SUCCEEDED task.
	Restores map[string]TaskOutcome
	// Statuses maps a component id to the status fetchComponentStatuses
	// reports. A component absent from the map is reported Running.
	Statuses map[string]string
	// PollsBeforeTerminal is how many getTask calls a task answers with a
	// non-terminal status before it settles. Values below 1 are treated as 1.
	PollsBeforeTerminal int
}

// Recorded is one request as the mock saw it.
type Recorded struct {
	Seq         int
	OperationID string
	Method      string
	Path        string
	RawQuery    string
	Query       url.Values
	Header      http.Header
	Body        []byte
	// Violation is empty when the request honoured the contract, and
	// otherwise says how it did not.
	Violation string
}

// BodyJSON decodes the recorded request body into a generic JSON value.
func (r Recorded) BodyJSON() (any, error) {
	var v any
	if err := json.Unmarshal(r.Body, &v); err != nil {
		return nil, fmt.Errorf("request %d (%s): body is not JSON: %w", r.Seq, r.OperationID, err)
	}
	return v, nil
}

// Server is a running mock. Close it when the test is done with it.
type Server struct {
	// URL is the loopback base URL the client should be pointed at.
	URL string

	opts     Options
	contract *contractDoc
	governed []string
	http     *httptest.Server

	mu      sync.Mutex
	seq     int
	log     []Recorded
	tasks   map[string]*taskState
	taskNum int
}

type taskState struct {
	id          string
	componentID string
	polls       int
	outcome     TaskOutcome
}

// Start loads the contract, builds the route table from it and starts the mock
// on loopback.
func Start(opts Options) (*Server, error) {
	doc, err := loadContract(opts.ContractPath)
	if err != nil {
		return nil, err
	}
	if opts.PollsBeforeTerminal < 1 {
		opts.PollsBeforeTerminal = 1
	}
	s := &Server{
		opts:     opts,
		contract: doc,
		governed: doc.governedHeaders(),
		tasks:    map[string]*taskState{},
	}
	s.http = httptest.NewServer(http.HandlerFunc(s.serve))
	s.URL = s.http.URL
	return s, nil
}

// Close shuts the mock down.
func (s *Server) Close() {
	s.http.Close()
}

// Log returns every request the mock received, in arrival order.
func (s *Server) Log() []Recorded {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Recorded, len(s.log))
	copy(out, s.log)
	return out
}

// Sequence returns the operationId of every request in arrival order.
// Unmatched requests appear as "<unrouted>".
func (s *Server) Sequence() []string {
	var out []string
	for _, r := range s.Log() {
		if r.OperationID == "" {
			out = append(out, "<unrouted>")
			continue
		}
		out = append(out, r.OperationID)
	}
	return out
}

// Requests returns every recorded request that matched the named operation.
func (s *Server) Requests(operationID string) []Recorded {
	var out []Recorded
	for _, r := range s.Log() {
		if r.OperationID == operationID {
			out = append(out, r)
		}
	}
	return out
}

// Violations returns a description of every request that broke the contract.
func (s *Server) Violations() []string {
	var out []string
	for _, r := range s.Log() {
		if r.Violation != "" {
			out = append(out, fmt.Sprintf("request %d %s %s: %s", r.Seq, r.Method, r.Path, r.Violation))
		}
	}
	return out
}

// AuthorizationHeader is the exact Authorization value the contract's security
// scheme calls for.
func (s *Server) AuthorizationHeader() string {
	return s.contract.Security.HTTPScheme + " " + s.opts.Token
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	body := readBody(r)

	rec := Recorded{
		Method:   r.Method,
		Path:     r.URL.Path,
		RawQuery: r.URL.RawQuery,
		Query:    r.URL.Query(),
		Header:   r.Header.Clone(),
		Body:     body,
	}

	op, opID, pathParams := s.route(r.Method, r.URL.Path)
	rec.OperationID = opID

	if op == nil {
		rec.Violation = fmt.Sprintf("no operation in the contract serves %s %s", r.Method, r.URL.Path)
		s.record(&rec)
		writeError(w, http.StatusNotFound, rec.Violation)
		return
	}

	if v := s.checkAuth(r); v != "" {
		rec.Violation = v
		s.record(&rec)
		writeError(w, http.StatusUnauthorized, v)
		return
	}
	if v := checkQuery(op, r.URL.Query()); v != "" {
		rec.Violation = v
		s.record(&rec)
		writeError(w, http.StatusBadRequest, v)
		return
	}
	if v := s.checkHeaders(op, r.Header); v != "" {
		rec.Violation = v
		s.record(&rec)
		writeError(w, http.StatusBadRequest, v)
		return
	}

	decoded, variant, v := s.checkBody(op, r, body)
	if v != "" {
		rec.Violation = v
		s.record(&rec)
		writeError(w, http.StatusBadRequest, v)
		return
	}

	status, payload, v := s.dispatch(opID, op, pathParams, r.URL.Query(), decoded, variant)
	if v != "" {
		rec.Violation = v
		s.record(&rec)
		writeError(w, http.StatusBadRequest, v)
		return
	}
	s.record(&rec)
	writeJSON(w, status, payload)
}

func (s *Server) record(rec *Recorded) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.seq++
	rec.Seq = s.seq
	s.log = append(s.log, *rec)
}

// route finds the contract operation serving method and path. Operations whose
// path is entirely literal win over operations with path parameters.
func (s *Server) route(method, path string) (*operation, string, map[string]string) {
	got := strings.Split(strings.TrimPrefix(path, "/"), "/")

	var bestOp *operation
	var bestID string
	var bestParams map[string]string
	bestLiterals := -1

	for id, op := range s.contract.Operations {
		if op.Method != method || len(op.segments) != len(got) {
			continue
		}
		params := map[string]string{}
		literals := 0
		ok := true
		for i, seg := range op.segments {
			if strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}") {
				name := seg[1 : len(seg)-1]
				if got[i] == "" {
					ok = false
					break
				}
				params[name] = got[i]
				continue
			}
			if seg != got[i] {
				ok = false
				break
			}
			literals++
		}
		if ok && literals > bestLiterals {
			bestOp, bestID, bestParams, bestLiterals = op, id, params, literals
		}
	}
	return bestOp, bestID, bestParams
}

func (s *Server) checkAuth(r *http.Request) string {
	want := s.AuthorizationHeader()
	got := r.Header.Get("Authorization")
	if got == "" {
		return "Authorization header is missing"
	}
	if got != want {
		return fmt.Sprintf("Authorization is %q, the contract's security scheme %q calls for %q",
			got, s.contract.Security.Scheme, want)
	}
	return ""
}

func checkQuery(op *operation, q url.Values) string {
	allowed := map[string]bool{}
	for _, name := range op.QueryParams.Required {
		allowed[name] = true
	}
	for _, name := range op.QueryParams.Optional {
		allowed[name] = true
	}
	var seen []string
	for name := range q {
		seen = append(seen, name)
	}
	sort.Strings(seen)
	for _, name := range seen {
		if !allowed[name] {
			return fmt.Sprintf("query parameter %q is not declared by the operation", name)
		}
		for _, value := range q[name] {
			if value == "" {
				return fmt.Sprintf("query parameter %q was sent empty; an unset optional parameter is omitted", name)
			}
		}
	}
	for _, name := range op.QueryParams.Required {
		if _, ok := q[name]; !ok {
			return fmt.Sprintf("required query parameter %q is missing", name)
		}
	}
	return ""
}

func (s *Server) checkHeaders(op *operation, h http.Header) string {
	declared := map[string]bool{}
	for _, name := range op.OptionalHeaders {
		declared[strings.ToLower(name)] = true
	}
	for _, name := range s.governed {
		if h.Get(name) == "" {
			continue
		}
		if !declared[strings.ToLower(name)] {
			return fmt.Sprintf("header %q is not declared by the operation", name)
		}
	}
	return ""
}

// checkBody validates the request body against the schema field split the
// contract publishes. It returns the decoded object and, for a discriminated
// request, the variant the body selected.
func (s *Server) checkBody(op *operation, r *http.Request, body []byte) (map[string]any, string, string) {
	schemas := s.contract.requestSchemasFor(op)
	if len(schemas) == 0 {
		if len(body) != 0 {
			return nil, "", "the operation takes no request body but one was sent"
		}
		return nil, "", ""
	}
	if ct := r.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		return nil, "", fmt.Sprintf("Content-Type is %q, want application/json", ct)
	}
	var obj map[string]any
	if err := json.Unmarshal(body, &obj); err != nil {
		return nil, "", fmt.Sprintf("request body is not a JSON object: %v", err)
	}

	schemaName := schemas[0]
	if op.RequestVariants != nil {
		raw, ok := obj[op.RequestVariants.Discriminator]
		if !ok {
			return nil, "", fmt.Sprintf("discriminator %q is missing from the request body",
				op.RequestVariants.Discriminator)
		}
		picked, ok := raw.(string)
		if !ok || !contains(op.RequestVariants.Variants, picked) {
			return nil, "", fmt.Sprintf("discriminator %q is %v, want one of %v",
				op.RequestVariants.Discriminator, raw, op.RequestVariants.Variants)
		}
		schemaName = picked
	}

	if v := s.validateObject(schemaName, obj, schemaName); v != "" {
		return nil, "", v
	}
	return obj, schemaName, ""
}

func (s *Server) validateObject(schemaName string, obj map[string]any, where string) string {
	set := s.contract.Schemas[schemaName]
	if set == nil {
		return fmt.Sprintf("the contract publishes no schema %q", schemaName)
	}
	var keys []string
	for k := range obj {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		if !contains(set.Required, k) && !contains(set.Optional, k) {
			return fmt.Sprintf("%s.%s is not a field of schema %s", where, k, schemaName)
		}
		if obj[k] == nil {
			return fmt.Sprintf("%s.%s was sent null; an unset optional field is omitted", where, k)
		}
	}
	for _, k := range set.Required {
		if _, ok := obj[k]; !ok {
			return fmt.Sprintf("%s is missing required field %q of schema %s", where, k, schemaName)
		}
	}
	for prop, itemSchema := range nestedItemSchemas[schemaName] {
		raw, ok := obj[prop]
		if !ok {
			continue
		}
		items, ok := raw.([]any)
		if !ok {
			return fmt.Sprintf("%s.%s must be an array", where, prop)
		}
		for i, item := range items {
			child, ok := item.(map[string]any)
			if !ok {
				return fmt.Sprintf("%s.%s[%d] must be an object", where, prop, i)
			}
			if v := s.validateObject(itemSchema, child, fmt.Sprintf("%s.%s[%d]", where, prop, i)); v != "" {
				return v
			}
		}
	}
	return ""
}

func (s *Server) dispatch(opID string, op *operation, pathParams map[string]string,
	q url.Values, body map[string]any, variant string) (int, any, string) {

	switch opID {
	case "getComponents":
		return op.SuccessStatus, s.handleGetComponents(q), ""
	case "getComponentsBackups":
		payload, v := s.handleGetBackups(q)
		return op.SuccessStatus, payload, v
	case "backupRestoreComponentsAction":
		payload, v := s.handleAction(body, variant)
		return op.SuccessStatus, payload, v
	case "getTask":
		payload, v := s.handleGetTask(pathParams["taskId"])
		return op.SuccessStatus, payload, v
	case "fetchComponentStatuses":
		return op.SuccessStatus, s.handleStatuses(body), ""
	}
	return 0, nil, fmt.Sprintf("the mock cannot simulate operation %q", opID)
}

func (s *Server) handleGetComponents(q url.Values) any {
	scope := q.Get("scope")
	out := []Component{}
	for _, c := range s.opts.Components {
		if scope != "" && c.Scope != scope {
			continue
		}
		out = append(out, c)
	}
	return map[string]any{"components": out}
}

func (s *Server) handleGetBackups(q url.Values) (any, string) {
	componentID := q.Get("componentId")

	var start, end time.Time
	if raw := q.Get("periodStart"); raw != "" {
		t, err := time.Parse(time.RFC3339, raw)
		if err != nil {
			return nil, fmt.Sprintf("periodStart %q is not an RFC 3339 timestamp", raw)
		}
		start = t
	}
	if raw := q.Get("periodEnd"); raw != "" {
		t, err := time.Parse(time.RFC3339, raw)
		if err != nil {
			return nil, fmt.Sprintf("periodEnd %q is not an RFC 3339 timestamp", raw)
		}
		end = t
	}

	out := []Backup{}
	for _, b := range s.opts.Backups {
		if componentID != "" && b.ComponentID != componentID {
			continue
		}
		at, err := time.Parse(time.RFC3339, b.At)
		if err != nil {
			return nil, fmt.Sprintf("mock backup %q has an unparsable timestamp %q", b.Name, b.At)
		}
		if !start.IsZero() && at.Before(start) {
			continue
		}
		if !end.IsZero() && at.After(end) {
			continue
		}
		out = append(out, b)
	}
	return map[string]any{"backups": out}, ""
}

func (s *Server) handleAction(body map[string]any, variant string) (any, string) {
	componentID, v := targetComponent(body, variant)
	if v != "" {
		return nil, v
	}

	s.mu.Lock()
	defer s.mu.Unlock()
	s.taskNum++
	id := fmt.Sprintf("00000000-0000-4000-8000-%012d", s.taskNum)
	outcome, ok := s.opts.Restores[componentID]
	if !ok {
		outcome = TaskOutcome{Status: "SUCCEEDED"}
	}
	s.tasks[id] = &taskState{id: id, componentID: componentID, outcome: outcome}

	return map[string]any{
		"id":           id,
		"name":         "component-" + strings.ToLower(variant),
		"status":       "PENDING",
		"type":         variant,
		"resourceId":   componentID,
		"resourceType": "COMPONENT",
		"cancellable":  true,
	}, ""
}

func targetComponent(body map[string]any, variant string) (string, string) {
	switch variant {
	case "ComponentsRestoreSpec":
		items, _ := body["components"].([]any)
		if len(items) != 1 {
			return "", fmt.Sprintf("this service restores one component per task, got %d", len(items))
		}
		first, _ := items[0].(map[string]any)
		id, _ := first["componentId"].(string)
		if id == "" {
			return "", "components[0].componentId is required to raise a restore task"
		}
		return id, ""
	case "ComponentsBackupSpec":
		items, _ := body["componentIds"].([]any)
		if len(items) != 1 {
			return "", fmt.Sprintf("this service backs up one component per task, got %d", len(items))
		}
		id, _ := items[0].(string)
		if id == "" {
			return "", "componentIds[0] must be a component id"
		}
		return id, ""
	}
	return "", fmt.Sprintf("unsupported request variant %q", variant)
}

func (s *Server) handleGetTask(taskID string) (any, string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	st, ok := s.tasks[taskID]
	if !ok {
		return nil, fmt.Sprintf("no task %q was raised by this service", taskID)
	}
	st.polls++
	if st.polls < s.opts.PollsBeforeTerminal {
		status := "RUNNING"
		if st.polls == 1 {
			status = "SCHEDULED"
		}
		return map[string]any{
			"id":           st.id,
			"name":         "component-restore",
			"status":       status,
			"resourceId":   st.componentID,
			"resourceType": "COMPONENT",
			"stages": []any{
				stage("stage-precheck", "restore-precheck", "SUCCEEDED", nil),
				stage("stage-restore", "restore-data", "RUNNING", nil),
			},
		}, ""
	}
	return s.terminalTask(st), ""
}

func (s *Server) terminalTask(st *taskState) any {
	out := map[string]any{
		"id":           st.id,
		"name":         "component-restore",
		"status":       st.outcome.Status,
		"resourceId":   st.componentID,
		"resourceType": "COMPONENT",
		"endTime":      "2026-02-11T09:14:52Z",
	}

	taskMessages := []any{
		message("INFO", "com.broadcom.lcm.restore.started", "Restore started"),
	}

	if st.outcome.FailedStage != "" {
		stages := []any{}
		for _, def := range []struct{ id, name string }{
			{"stage-precheck", "restore-precheck"},
			{"stage-restore", "restore-data"},
			{"stage-verify", "restore-verify"},
		} {
			switch {
			case def.name == st.outcome.FailedStage:
				msgs := []any{
					message("INFO", "com.broadcom.lcm.restore.stage.started", "Stage started"),
					message("WARN", "com.broadcom.lcm.restore.stage.slow", "Stage is running slowly"),
				}
				for _, e := range st.outcome.Errors {
					msgs = append(msgs, message("ERROR", e.ID, e.DefaultMessage))
				}
				stages = append(stages, stage(def.id, def.name, "FAILED", msgs))
			case len(stages) == 0 || !stageFailedYet(stages):
				stages = append(stages, stage(def.id, def.name, "SUCCEEDED", nil))
			default:
				stages = append(stages, stage(def.id, def.name, "SKIPPED", nil))
			}
		}
		out["stages"] = stages
		// A decoy: a task level ERROR that is not the failed stage's error.
		taskMessages = append(taskMessages,
			message("ERROR", "com.broadcom.lcm.restore.rollup", "The restore task did not complete"))
	} else {
		out["stages"] = []any{
			stage("stage-precheck", "restore-precheck", "SUCCEEDED", nil),
			stage("stage-restore", "restore-data", statusOfStage(st.outcome.Status), nil),
		}
		for _, e := range st.outcome.Errors {
			taskMessages = append(taskMessages, message("ERROR", e.ID, e.DefaultMessage))
		}
	}

	out["messages"] = taskMessages
	return out
}

func stageFailedYet(stages []any) bool {
	for _, raw := range stages {
		if m, ok := raw.(map[string]any); ok && m["status"] == "FAILED" {
			return true
		}
	}
	return false
}

func statusOfStage(taskStatus string) string {
	switch taskStatus {
	case "SUCCEEDED":
		return "SUCCEEDED"
	case "CANCELED":
		return "CANCELED"
	}
	return "FAILED"
}

func stage(id, name, status string, messages []any) map[string]any {
	out := map[string]any{"id": id, "name": name, "status": status}
	if messages != nil {
		out["messages"] = messages
	}
	return out
}

func message(level, id, text string) map[string]any {
	return map[string]any{
		"level":     level,
		"timestamp": "2026-02-11T09:14:52Z",
		"message":   map[string]any{"id": id, "defaultMessage": text},
	}
}

func (s *Server) handleStatuses(body map[string]any) any {
	ids, _ := body["componentIds"].([]any)
	out := []any{}
	for _, raw := range ids {
		id, _ := raw.(string)
		status, ok := s.opts.Statuses[id]
		if !ok {
			status = "Running"
		}
		out = append(out, map[string]any{"id": id, "status": status})
	}
	return map[string]any{"componentStatuses": out}
}

func readBody(r *http.Request) []byte {
	if r.Body == nil {
		return nil
	}
	defer r.Body.Close()
	buf := make([]byte, 0, 1024)
	tmp := make([]byte, 1024)
	for {
		n, err := r.Body.Read(tmp)
		buf = append(buf, tmp[:n]...)
		if err != nil {
			break
		}
	}
	return buf
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func writeError(w http.ResponseWriter, status int, detail string) {
	writeJSON(w, status, map[string]any{
		"code":        "CONTRACT_VIOLATION",
		"message":     map[string]any{"id": "com.broadcom.lcm.mock.violation", "defaultMessage": detail},
		"resolution":  map[string]any{"id": "com.broadcom.lcm.mock.resolution", "defaultMessage": "Send the request the contract describes."},
		"referenceId": "mock",
		"timestamp":   "2026-02-11T09:14:52Z",
		"detail":      detail,
	})
}
