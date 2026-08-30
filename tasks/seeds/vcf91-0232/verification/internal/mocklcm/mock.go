package mocklcm

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strings"
	"sync"
	"time"
)

// Record is one request the mock received, kept exactly as it arrived.
type Record struct {
	Seq int `json:"seq"`
	// OperationID is the operation the contract routed the request to, or ""
	// when nothing matched.
	OperationID string `json:"operationId"`
	Method      string `json:"method"`
	// Target is the request target as sent: the path, and the query string
	// exactly as it appeared, including a bare "?" if the client sent one.
	Target   string              `json:"target"`
	Path     string              `json:"path"`
	RawQuery string              `json:"rawQuery"`
	Query    map[string][]string `json:"query"`
	Header   map[string][]string `json:"header"`
	BodyRaw  string              `json:"bodyRaw"`
	// Body is the decoded JSON body, nil when the request carried none.
	Body   map[string]any `json:"body"`
	Status int            `json:"status"`
	// Token is the bearer credential the request presented, without the scheme.
	Token string `json:"token"`
	// Violation is set when the request broke the contract.
	Violation string `json:"violation,omitempty"`
}

// HeaderPresent reports whether the request carried the named header at all.
// A header sent with an empty value is present.
func (r Record) HeaderPresent(name string) bool {
	_, ok := r.Header[http.CanonicalHeaderKey(name)]
	return ok
}

// HeaderValue returns the single value of a header, or "" when it was absent.
func (r Record) HeaderValue(name string) string {
	v := r.Header[http.CanonicalHeaderKey(name)]
	if len(v) == 0 {
		return ""
	}
	return v[0]
}

// Config configures a mock.
type Config struct {
	// ContractPath is the derived contract the route table is built from.
	ContractPath string
	// Tokens are the bearer credentials the service accepts, in the order a
	// client is expected to obtain them. A credential outside this set is
	// rejected.
	Tokens []string
	// TokenUses is how many authenticated requests each credential serves
	// before it is treated as expired. Zero or less means credentials never
	// expire.
	TokenUses int
	Inventory []InventoryComponent
	Depot     []DepotEntry
	Task      TaskScript
	// TaskID is the identifier the raised task carries.
	TaskID string
}

// Mock is a loopback SDDC LCM service pinned to a derived contract.
type Mock struct {
	contract *Contract
	ln       net.Listener
	srv      *http.Server
	url      string

	tokens    map[string]bool
	tokenUses int

	mu         sync.Mutex
	seq        int
	records    []Record
	violations []string
	uses       map[string]int
	inventory  []InventoryComponent
	depot      []DepotEntry
	taskID     string
	script     TaskScript
	active     *TaskScript
	pollIdx    int
	raised     bool
}

// New starts a mock on 127.0.0.1. The caller must Close it.
func New(cfg Config) (*Mock, error) {
	contract, err := LoadContract(cfg.ContractPath)
	if err != nil {
		return nil, err
	}
	if len(cfg.Tokens) == 0 {
		return nil, fmt.Errorf("mocklcm: no tokens configured")
	}
	taskID := cfg.TaskID
	if taskID == "" {
		taskID = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
	}
	m := &Mock{
		contract:  contract,
		tokens:    map[string]bool{},
		tokenUses: cfg.TokenUses,
		uses:      map[string]int{},
		inventory: cfg.Inventory,
		depot:     cfg.Depot,
		taskID:    taskID,
		script:    cfg.Task,
	}
	for _, t := range cfg.Tokens {
		m.tokens[t] = true
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, err
	}
	m.ln = ln
	m.url = "http://" + ln.Addr().String()
	m.srv = &http.Server{
		Handler:           http.HandlerFunc(m.serve),
		ReadHeaderTimeout: 10 * time.Second,
	}
	go func() { _ = m.srv.Serve(ln) }()
	return m, nil
}

// URL is the base URL of the running mock.
func (m *Mock) URL() string { return m.url }

// Close stops the mock.
func (m *Mock) Close() error { return m.srv.Close() }

// Requests returns everything the mock received, in arrival order.
func (m *Mock) Requests() []Record {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]Record, len(m.records))
	copy(out, m.records)
	return out
}

// Violations returns the contract breaches the mock saw.
func (m *Mock) Violations() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	return append([]string(nil), m.violations...)
}

// OperationCounts counts the requests routed to each operation, whatever status
// they were answered with.
func (m *Mock) OperationCounts() map[string]int {
	out := map[string]int{}
	for _, r := range m.Requests() {
		if r.OperationID != "" {
			out[r.OperationID]++
		}
	}
	return out
}

// WriteLog writes the request log as JSON lines, flushed and synced, so another
// process can read it after the mock stops.
func (m *Mock) WriteLog(path string) error {
	recs := m.Requests()
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	enc := json.NewEncoder(f)
	for _, r := range recs {
		if err := enc.Encode(r); err != nil {
			f.Close()
			return err
		}
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	return f.Close()
}

// contractHeaders is the set of headers the specification declares as operation
// parameters. Anything outside this set is transport noise the mock ignores.
func (m *Mock) contractHeaders() map[string]bool {
	out := map[string]bool{}
	for _, op := range m.contract.Operations {
		for _, h := range op.OptionalHeaders {
			out[http.CanonicalHeaderKey(h)] = true
		}
	}
	return out
}

func (m *Mock) serve(w http.ResponseWriter, r *http.Request) {
	body := readAll(r)
	rec := Record{
		Method:   r.Method,
		Target:   r.URL.RequestURI(),
		Path:     r.URL.Path,
		RawQuery: r.URL.RawQuery,
		Query:    map[string][]string{},
		Header:   map[string][]string{},
		BodyRaw:  string(body),
	}
	// url.ParseQuery drops nothing, so a parameter sent with an empty value is
	// visible as such.
	if q, err := url.ParseQuery(r.URL.RawQuery); err == nil {
		for k, v := range q {
			rec.Query[k] = append([]string(nil), v...)
		}
	}
	for k, v := range r.Header {
		rec.Header[k] = append([]string(nil), v...)
	}
	if auth := r.Header.Get("Authorization"); auth != "" {
		prefix := m.contract.Security.HTTPScheme + " "
		if strings.HasPrefix(auth, prefix) {
			rec.Token = strings.TrimPrefix(auth, prefix)
		}
	}
	if len(body) > 0 {
		var decoded map[string]any
		if err := json.Unmarshal(body, &decoded); err == nil {
			rec.Body = decoded
		}
	}

	status, payload, violation := m.dispatch(&rec, r, body)
	rec.Status = status
	rec.Violation = violation

	m.mu.Lock()
	m.seq++
	rec.Seq = m.seq
	m.records = append(m.records, rec)
	if violation != "" {
		m.violations = append(m.violations,
			fmt.Sprintf("request %d %s %s: %s", rec.Seq, rec.Method, rec.Target, violation))
	}
	m.mu.Unlock()

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

// dispatch routes, authenticates, polices and answers a request. It returns the
// status, the response payload and a contract violation if there was one.
func (m *Mock) dispatch(rec *Record, r *http.Request, body []byte) (int, any, string) {
	opID, pathParams, ok := m.contract.OperationID(r.Method, r.URL.Path, rec.Query)
	if !ok {
		return http.StatusNotFound, errorPayload("LCM_ROUTE_NOT_FOUND",
				"No operation is served at this target."),
			fmt.Sprintf("no contract operation serves %s %s", r.Method, r.URL.RequestURI())
	}
	rec.OperationID = opID
	op := m.contract.Operations[opID]

	// Credential. The specification's single security scheme is an HTTP bearer
	// token, so the header is "<httpScheme> <token>".
	if code, msg, bad := m.checkToken(rec.Token, r.Header.Get("Authorization")); bad {
		return http.StatusUnauthorized, errorPayload(code, msg), ""
	}

	if v := m.checkQuery(op, rec); v != "" {
		return http.StatusBadRequest, errorPayload("LCM_INVALID_QUERY", v), v
	}
	if v := m.checkHeaders(op, rec); v != "" {
		return http.StatusBadRequest, errorPayload("LCM_INVALID_HEADER", v), v
	}
	if v := m.checkBody(op, rec, body); v != "" {
		return http.StatusBadRequest, errorPayload("LCM_INVALID_BODY", v), v
	}

	switch opID {
	case "getComponents":
		return op.SuccessStatus, m.handleGetComponents(rec), ""
	case "resolveDepotComponents":
		return m.handleResolveDepot(op, rec)
	case "createComponents":
		return m.handleCreateComponents(op, rec)
	case "getTask":
		return m.handleGetTask(op, pathParams)
	case "retryTask":
		return m.handleRetryTask(op, pathParams)
	default:
		v := fmt.Sprintf("operation %s is named by the contract but not served", opID)
		return http.StatusNotImplemented, errorPayload("LCM_NOT_IMPLEMENTED", v), v
	}
}

// checkToken applies the credential lifetime. A credential the service never
// issued, and one that has served its quota, are both rejected with 401.
func (m *Mock) checkToken(token, rawHeader string) (string, string, bool) {
	if rawHeader == "" {
		return "LCM_UNAUTHENTICATED", "No credential was presented.", true
	}
	if token == "" {
		return "LCM_UNAUTHENTICATED",
			fmt.Sprintf("The Authorization header must use the %s scheme.", m.contract.Security.HTTPScheme), true
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if !m.tokens[token] {
		return "LCM_TOKEN_INVALID", "The access token is not recognised.", true
	}
	if m.tokenUses > 0 {
		if m.uses[token] >= m.tokenUses {
			return "LCM_TOKEN_EXPIRED", "The access token has expired. Obtain a new one and retry.", true
		}
		m.uses[token]++
	}
	return "", "", false
}

func (m *Mock) checkQuery(op Operation, rec *Record) string {
	allowed := map[string]bool{}
	for k := range op.FixedQuery {
		allowed[k] = true
	}
	for _, k := range op.QueryParams.Required {
		allowed[k] = true
	}
	for _, k := range op.QueryParams.Optional {
		allowed[k] = true
	}
	names := make([]string, 0, len(rec.Query))
	for k := range rec.Query {
		names = append(names, k)
	}
	sort.Strings(names)
	for _, k := range names {
		if !allowed[k] {
			return fmt.Sprintf("query parameter %q is not declared by %s", k, rec.OperationID)
		}
		for _, v := range rec.Query[k] {
			if v == "" {
				return fmt.Sprintf("query parameter %q was sent with an empty value; an unset optional parameter is omitted", k)
			}
		}
	}
	for _, k := range op.QueryParams.Required {
		if len(rec.Query[k]) == 0 {
			return fmt.Sprintf("required query parameter %q is missing", k)
		}
	}
	// A trailing "?" with nothing after it is an empty query string, not an
	// absent one.
	if rec.RawQuery == "" && strings.HasSuffix(rec.Target, "?") {
		return "the request target ends in a bare \"?\"; a request with no query parameters sends no query string"
	}
	return ""
}

func (m *Mock) checkHeaders(op Operation, rec *Record) string {
	declared := map[string]bool{}
	for _, h := range op.OptionalHeaders {
		declared[http.CanonicalHeaderKey(h)] = true
	}
	for name := range m.contractHeaders() {
		values, present := rec.Header[name]
		if !present {
			continue
		}
		if !declared[name] {
			return fmt.Sprintf("header %s is not declared by %s", name, rec.OperationID)
		}
		for _, v := range values {
			if strings.TrimSpace(v) == "" {
				return fmt.Sprintf("header %s was sent empty; an unset optional header is omitted", name)
			}
		}
	}
	return ""
}

func (m *Mock) checkBody(op Operation, rec *Record, body []byte) string {
	if op.RequestSchema == nil {
		if len(body) > 0 {
			return fmt.Sprintf("%s takes no request body but %d bytes were sent", rec.OperationID, len(body))
		}
		return ""
	}
	if len(body) == 0 {
		return fmt.Sprintf("%s requires a %s body", rec.OperationID, *op.RequestSchema)
	}
	if ct := rec.HeaderValue("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		return fmt.Sprintf("%s must be sent as application/json, got %q", rec.OperationID, ct)
	}
	if rec.Body == nil {
		return fmt.Sprintf("%s body is not a JSON object", rec.OperationID)
	}
	return m.checkObject(*op.RequestSchema, rec.Body, *op.RequestSchema, op)
}

// bodyChild describes how a field of a request schema nests.
type bodyChild struct {
	array         bool
	schema        string
	discriminated bool
}

// bodyShape is the mock's own knowledge of how the request schemas nest. The
// required and optional field split for each schema comes from the contract, not
// from here.
var bodyShape = map[string]map[string]bodyChild{
	"DepotComponentsSpec": {
		"fleetDepotSpec":    {schema: "FleetDepotSpec"},
		"componentVersions": {array: true, schema: "ComponentVersionSpec"},
	},
	"ComponentSpecs": {
		"componentSpecs": {array: true, discriminated: true},
	},
	"ComponentImportSpec": {
		"repository": {schema: "ComponentRepository"},
	},
}

func (m *Mock) checkObject(schemaName string, obj map[string]any, where string, op Operation) string {
	schema, ok := m.contract.Schemas[schemaName]
	if !ok {
		return fmt.Sprintf("%s: the contract defines no schema %q", where, schemaName)
	}
	keys := make([]string, 0, len(obj))
	for k := range obj {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		if !schema.Known(k) {
			return fmt.Sprintf("%s: field %q is not part of %s", where, k, schemaName)
		}
		if v := emptyEncoding(obj[k]); v != "" {
			return fmt.Sprintf("%s: field %q was sent as %s; a field with no value is omitted, not sent empty",
				where, k, v)
		}
	}
	for _, req := range schema.Required {
		if _, present := obj[req]; !present {
			return fmt.Sprintf("%s: required field %q of %s is missing", where, req, schemaName)
		}
	}
	for field, child := range bodyShape[schemaName] {
		raw, present := obj[field]
		if !present {
			continue
		}
		if !child.array {
			nested, ok := raw.(map[string]any)
			if !ok {
				return fmt.Sprintf("%s: field %q is not a JSON object", where, field)
			}
			if v := m.checkObject(child.schema, nested, where+"."+field, op); v != "" {
				return v
			}
			continue
		}
		items, ok := raw.([]any)
		if !ok {
			return fmt.Sprintf("%s: field %q is not a JSON array", where, field)
		}
		for i, item := range items {
			nested, ok := item.(map[string]any)
			if !ok {
				return fmt.Sprintf("%s.%s[%d] is not a JSON object", where, field, i)
			}
			at := fmt.Sprintf("%s.%s[%d]", where, field, i)
			target := child.schema
			if child.discriminated {
				name, v := m.selectVariant(op, nested, at)
				if v != "" {
					return v
				}
				target = name
			}
			if v := m.checkObject(target, nested, at, op); v != "" {
				return v
			}
		}
	}
	return ""
}

// selectVariant applies the specification's discriminator to pick the schema an
// item of a discriminated body must satisfy.
func (m *Mock) selectVariant(op Operation, obj map[string]any, where string) (string, string) {
	if op.RequestVariants == nil {
		return "", fmt.Sprintf("%s: the contract declares no request variants for this operation", where)
	}
	raw, present := obj[op.RequestVariants.Discriminator]
	if !present {
		return "", fmt.Sprintf("%s: discriminator %q is missing", where, op.RequestVariants.Discriminator)
	}
	name, ok := raw.(string)
	if !ok {
		return "", fmt.Sprintf("%s: discriminator %q is not a string", where, op.RequestVariants.Discriminator)
	}
	if !contains(op.RequestVariants.Variants, name) {
		return "", fmt.Sprintf("%s: discriminator %q selects %q, which is not one of %v",
			where, op.RequestVariants.Discriminator, name, op.RequestVariants.Variants)
	}
	if _, ok := m.contract.Schemas[name]; !ok {
		return "", fmt.Sprintf("%s: this service serves only the variants the contract defines a schema for; %q is not one of them",
			where, name)
	}
	return name, ""
}

// emptyEncoding names the way a value encodes "no value", or "" when the value
// carries something. An unset optional field must be omitted, so any of these
// reaching the wire is a contract breach.
func emptyEncoding(v any) string {
	switch t := v.(type) {
	case nil:
		return "null"
	case string:
		if t == "" {
			return "an empty string"
		}
	case []any:
		if len(t) == 0 {
			return "an empty array"
		}
	case map[string]any:
		if len(t) == 0 {
			return "an empty object"
		}
	}
	return ""
}

func (m *Mock) handleGetComponents(rec *Record) any {
	scope := ""
	if v := rec.Query["scope"]; len(v) > 0 {
		scope = v[0]
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []any{}
	for _, c := range m.inventory {
		if scope != "" && c.Scope != scope {
			continue
		}
		out = append(out, map[string]any{
			"id":             c.ID,
			"componentType":  c.ComponentType,
			"deploymentType": c.DeploymentType,
			"version":        c.Version,
			"fqdn":           c.FQDN,
			"scope":          c.Scope,
		})
	}
	return map[string]any{"components": out}
}

func (m *Mock) handleResolveDepot(op Operation, rec *Record) (int, any, string) {
	requested, ok := rec.Body["componentVersions"].([]any)
	if !ok {
		v := "resolveDepotComponents: componentVersions is not an array"
		return http.StatusBadRequest, errorPayload("LCM_INVALID_BODY", v), v
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []any{}
	for _, item := range requested {
		spec, _ := item.(map[string]any)
		name, _ := spec["component"].(string)
		var found *DepotEntry
		for i := range m.depot {
			if m.depot[i].Component == name {
				found = &m.depot[i]
				break
			}
		}
		if found == nil {
			// The depot answers about what it has; a component it does not
			// carry simply does not come back.
			continue
		}
		entry := map[string]any{"component": found.Component}
		if found.Version != "" {
			entry["version"] = found.Version
		}
		if found.BinaryURL != "" {
			entry["binaryUrl"] = found.BinaryURL
		}
		out = append(out, entry)
	}
	return op.SuccessStatus, map[string]any{"componentVersions": out}, ""
}

func (m *Mock) handleCreateComponents(op Operation, rec *Record) (int, any, string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.raised {
		// Raising the same install twice is exactly the lost work this service
		// is being driven to expose.
		v := "createComponents was called again while a task was already raised for this run"
		return http.StatusConflict, errorPayload("LCM_TASK_ALREADY_RAISED", v), v
	}
	m.raised = true
	script := m.script
	m.active = &script
	m.pollIdx = 0
	return op.SuccessStatus, m.taskJSON(script.Accepted, &script), ""
}

func (m *Mock) handleGetTask(op Operation, params map[string]string) (int, any, string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if params["taskId"] != m.taskID {
		return http.StatusNotFound, errorPayload("LCM_TASK_NOT_FOUND",
			fmt.Sprintf("No task %s exists.", params["taskId"])), ""
	}
	if m.active == nil {
		return http.StatusNotFound, errorPayload("LCM_TASK_NOT_FOUND", "No task has been raised."), ""
	}
	status := statusAt(m.active.Poll, m.pollIdx)
	m.pollIdx++
	return op.SuccessStatus, m.taskJSON(status, m.active), ""
}

func (m *Mock) handleRetryTask(op Operation, params map[string]string) (int, any, string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if params["taskId"] != m.taskID {
		return http.StatusNotFound, errorPayload("LCM_TASK_NOT_FOUND",
			fmt.Sprintf("No task %s exists.", params["taskId"])), ""
	}
	if m.active == nil {
		return http.StatusBadRequest, errorPayload("LCM_TASK_NOT_RETRIABLE", "No task has been raised."), ""
	}
	if m.active.OnRetry == nil {
		return http.StatusBadRequest, errorPayload("LCM_TASK_NOT_RETRIABLE",
			"The task is not in a state that can be retried."), ""
	}
	m.active = m.active.OnRetry
	m.pollIdx = 0
	return op.SuccessStatus, m.taskJSON(m.active.Accepted, m.active), ""
}

// statusAt returns the status for a poll index, repeating the last entry.
func statusAt(poll []string, idx int) string {
	if len(poll) == 0 {
		return StatusSucceeded
	}
	if idx >= len(poll) {
		idx = len(poll) - 1
	}
	return poll[idx]
}

// taskJSON renders a Task as the specification shapes it.
func (m *Mock) taskJSON(status string, script *TaskScript) map[string]any {
	task := map[string]any{
		"id":           m.taskID,
		"name":         "vcf_fleet_component_install",
		"type":         "install",
		"status":       status,
		"resourceType": "COMPONENT",
		"createdBy":    "admin",
		"createTime":   "2026-03-01T10:00:00.000Z",
		"cancellable":  status == StatusPending || status == StatusRunning || status == StatusScheduled,
		"retriable":    status == StatusFailed && script.Retriable,
	}
	stages := []any{
		map[string]any{
			"id":       "stage-binary-download",
			"name":     "binary-download",
			"status":   StatusSucceeded,
			"messages": []any{},
		},
	}
	deploy := map[string]any{
		"id":       "stage-package-deploy",
		"name":     "package-deploy",
		"status":   status,
		"messages": []any{},
	}
	if status == StatusFailed && script.Failure != nil {
		msgs := []any{
			map[string]any{
				"level":     "INFO",
				"stageId":   "stage-package-deploy",
				"timestamp": "2026-03-01T10:07:00.000Z",
				"message": map[string]any{
					"id":               "com.broadcom.lcm.ops.component.deploy.started",
					"defaultMessage":   "Deployment started.",
					"localizedMessage": "Deployment started.",
				},
			},
		}
		for _, e := range script.Failure.Errors {
			msgs = append(msgs, map[string]any{
				"level":     "ERROR",
				"stageId":   "stage-package-deploy",
				"timestamp": "2026-03-01T10:09:00.000Z",
				"message": map[string]any{
					"id":               e.ID,
					"defaultMessage":   e.DefaultMessage,
					"localizedMessage": e.DefaultMessage,
				},
			})
		}
		if script.Failure.Stage == "package-deploy" {
			deploy["messages"] = msgs
			deploy["status"] = StatusFailed
		} else {
			// No stage is marked failed; the errors sit on the task itself.
			deploy["status"] = StatusSucceeded
			task["messages"] = msgs
		}
	}
	stages = append(stages, deploy)
	task["stages"] = stages
	if _, ok := task["messages"]; !ok {
		task["messages"] = []any{}
	}
	return task
}

func errorPayload(code, message string) map[string]any {
	msg := map[string]any{
		"id":               "com.broadcom.lcm." + strings.ToLower(code),
		"defaultMessage":   message,
		"localizedMessage": message,
	}
	return map[string]any{
		"code":        code,
		"message":     msg,
		"resolution":  msg,
		"referenceId": "ref-" + strings.ToLower(code),
		"timestamp":   "2026-03-01T10:00:00.000Z",
	}
}

func readAll(r *http.Request) []byte {
	if r.Body == nil {
		return nil
	}
	defer r.Body.Close()
	var buf []byte
	tmp := make([]byte, 4096)
	for {
		n, err := r.Body.Read(tmp)
		buf = append(buf, tmp[:n]...)
		if err != nil {
			break
		}
	}
	return buf
}
