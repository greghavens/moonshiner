// Package contractmock serves a loopback stand-in for the credential surface of
// a VMware Cloud Foundation 9.0 SDDC Manager appliance.
//
// The route table is loaded from docs/contract.json at startup, so the mock can
// only ever answer the operations that contract names. Anything else is refused
// and recorded as a contract violation, as is a request whose headers, target
// or body shape departs from the contract. Every request is appended to a
// mutex-guarded log that a test can read with Requests.
//
// Nothing here contacts a live VMware endpoint. The mock prefers an ephemeral
// 127.0.0.1 listener and falls back to an in-process HTTP transport when the
// verification sandbox disables sockets.
package contractmock

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"sync"
	"testing"
)

// Fixture identifiers handed back by the mock. Tests assert against these, and
// the client under test must thread them through instead of inventing its own.
const (
	AccessToken = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.sddc-manager-9-0-credentials-fixture"

	// TaskID deliberately carries a space and a slash. The appliance is free to
	// hand back any string, so a client that pastes it into a URL without
	// escaping it as a single path segment will not reach this mock.
	TaskID = "credtask 5f2b/9c1d"

	ESXiHost = "esx-a07.vcf.local"
	VCenter  = "vcenter-a01.vcf.local"

	ESXiRootUser = "root"
	ESXiSvcUser  = "svc-vcf-esx"
	VCenterUser  = "administrator@vsphere.local"

	VCenterResourceID = "d5e1a394-6c27-4f80-9b13-2a7c5e8d0f46"

	OldESXiRootPassword = "old-esx-root-Aa1!"
	OldESXiSvcPassword  = "old-esx-svc-Bb2!"
	OldVCenterPassword  = "old-vc-sso-Cc3!"

	NewESXiRootPassword = "new-esx-root-Dd4!"
	NewESXiSvcPassword  = "new-esx-svc-Ee5!"
	NewVCenterPassword  = "new-vc-sso-Ff6!"

	VCenterFailureErrorCode = "CREDENTIALS_ROTATE_REMOTE_FAILURE"
	VCenterFailureMessage   = "Rotation of administrator@vsphere.local on vcenter-a01.vcf.local failed: the identity source rejected the generated password."

	TaskFailureErrorCode = "CREDENTIALS_TASK_INCONSISTENT"
	TaskFailureMessage   = "The credentials task changed 2 of 3 credentials. Credentials that were changed are live on their resources."

	SubmitRejectedErrorCode = "CREDENTIALS_OPERATION_ALREADY_IN_PROGRESS"
	SubmitRejectedMessage   = "Another credentials task is already running on this appliance."

	PollRejectedErrorCode = "INTERNAL_SERVER_ERROR"
	PollRejectedMessage   = "The credentials service is temporarily unavailable."

	CancelRejectedErrorCode = "CREDENTIALS_CANCEL_FAILED"
	CancelRejectedMessage   = "The settled credentials task could not be retired."
)

// Scenario selects how the appliance behaves for one run.
type Scenario int

const (
	// ScenarioSucceeds settles the credentials task SUCCESSFUL with all three
	// credentials changed.
	ScenarioSucceeds Scenario = iota
	// ScenarioPartial settles the credentials task INCONSISTENT: the two ESXi
	// credentials were changed on the resource and the vCenter one was not.
	ScenarioPartial
	// ScenarioSubmitRejected refuses updateOrRotatePasswords with HTTP 400, so
	// nothing is changed anywhere.
	ScenarioSubmitRejected
	// ScenarioPollRejected accepts the change with HTTP 202 and then fails
	// getCredentialsTask with HTTP 500.
	ScenarioPollRejected
	// ScenarioAcceptedTerminal returns a terminal accepted Task, followed by a
	// terminal CredentialsTask on the required first poll.
	ScenarioAcceptedTerminal
	// ScenarioAcceptedTaskMissingName returns a 202 Task without its required
	// name member.
	ScenarioAcceptedTaskMissingName
	// ScenarioUnknownStatus reports a CredentialsTask status outside the pinned
	// vocabulary.
	ScenarioUnknownStatus
	// ScenarioUserCancelled settles USER_CANCELLED and must not be cancelled a
	// second time by the client.
	ScenarioUserCancelled
	// ScenarioSuccessfulBlankPassword settles successfully but reports a
	// whitespace-only replacement for one credential.
	ScenarioSuccessfulBlankPassword
	// ScenarioCancelRejected settles INCONSISTENT and then rejects the attempt
	// to retire that task.
	ScenarioCancelRejected
	// ScenarioMismatchedTaskID returns a polled task whose id differs from the
	// accepted Task id.
	ScenarioMismatchedTaskID
	// ScenarioPendingForever never leaves the nonterminal PENDING state.
	ScenarioPendingForever
)

// Hooks lets a test observe the appliance from inside a handler. They are
// supplied at Start so they are established before any request can arrive.
type Hooks struct {
	// BeforeSubmit runs at the top of the updateOrRotatePasswords handler,
	// before anything is recorded. It is the moment the old secrets stop being
	// usable, so a test can assert here that no caller still holds one.
	BeforeSubmit func()
}

// Request is one logged inbound HTTP request.
type Request struct {
	// OperationID is the contract operationId this request matched, or "" when
	// the request did not match any operation the contract names.
	OperationID string
	Method      string
	// RawTarget is r.RequestURI verbatim, so a stray query string or a bare "?"
	// is visible to the test.
	RawTarget string
	// EscapedPath is the on-the-wire path, still percent-encoded.
	EscapedPath string
	// Path is the decoded path.
	Path string
	// PathParams holds the decoded template parameters of the matched route.
	PathParams map[string]string
	Query      string
	Header     http.Header
	Body       []byte
	// Violations lists every way the request departed from docs/contract.json.
	// An empty list means the request was contract-clean.
	Violations []string
}

// Server is a running loopback SDDC Manager stand-in.
type Server struct {
	// URL is the service root, e.g. http://127.0.0.1:39481.
	URL string
	// HTTPClient reaches this mock through loopback when sockets are available
	// and through the same handler in process otherwise.
	HTTPClient *http.Client

	scenario Scenario
	hooks    Hooks
	http     *httptest.Server
	routes   []route
	contract contractFile

	mu      sync.Mutex
	log     []Request
	polls   int
	settled string
}

type route struct {
	operationID string
	method      string
	path        string
	params      []string
	pattern     *regexp.Regexp
	hasBody     bool
	bodySchema  string
	successCode int
}

type schema struct {
	Required []string          `json:"required"`
	ReadOnly []string          `json:"readOnly"`
	Members  map[string]member `json:"members"`
}

type member struct {
	Type  string `json:"type"`
	Items string `json:"items"`
}

type contractFile struct {
	Operations []struct {
		OperationID   string `json:"operationId"`
		Method        string `json:"method"`
		Path          string `json:"path"`
		SuccessStatus int    `json:"successStatus"`
		RequestBody   *struct {
			Schema   string `json:"schema"`
			Required bool   `json:"required"`
		} `json:"requestBody"`
	} `json:"operations"`
	Schemas     map[string]schema `json:"schemas"`
	Vocabulary  map[string]vocab  `json:"vocabularies"`
	RequestRule struct {
		RejectBlankStringMembers bool `json:"rejectBlankStringMembers"`
	} `json:"requestRules"`
}

type vocab struct {
	Schema  string   `json:"schema"`
	Member  string   `json:"member"`
	Allowed []string `json:"allowed"`
}

// handled names every operation this mock knows how to answer. Start fails if
// docs/contract.json names one that is missing here, so the contract and the
// appliance stand-in cannot drift apart.
var handled = map[string]bool{
	"updateOrRotatePasswords": true,
	"getCredentialsTask":      true,
	"cancelCredentialsTask":   true,
}

// Start boots the mock and registers cleanup. It prefers a real IPv4 loopback
// listener; an in-process RoundTripper keeps the verifier runnable in sandboxes
// that prohibit sockets while still exercising genuine HTTP requests.
func Start(t *testing.T, scenario Scenario, hooks Hooks) *Server {
	t.Helper()

	c := loadContract(t)
	s := &Server{scenario: scenario, hooks: hooks, contract: c}

	for _, op := range c.Operations {
		if !handled[op.OperationID] {
			t.Fatalf("contractmock: docs/contract.json names operation %q, which the mock cannot serve", op.OperationID)
		}
		pattern, params := pathPattern(op.Path)
		r := route{
			operationID: op.OperationID,
			method:      op.Method,
			path:        op.Path,
			params:      params,
			pattern:     pattern,
			successCode: op.SuccessStatus,
		}
		if op.RequestBody != nil {
			r.hasBody = true
			r.bodySchema = op.RequestBody.Schema
		}
		s.routes = append(s.routes, r)
	}
	if len(s.routes) != len(handled) {
		t.Fatalf("contractmock: docs/contract.json names %d operations, want %d", len(s.routes), len(handled))
	}

	handler := http.HandlerFunc(s.serve)
	ln, err := net.Listen("tcp4", "127.0.0.1:0")
	if err == nil {
		// Build the unstarted server around the listener we already own. Calling
		// httptest.NewUnstartedServer here would open a second listener first and
		// can still panic in environments that permit IPv4 but disable IPv6.
		s.http = &httptest.Server{
			Listener: ln,
			Config:   &http.Server{Handler: handler},
		}
		s.http.Start()
		s.URL = s.http.URL
		s.HTTPClient = s.http.Client()
		t.Cleanup(s.http.Close)
	} else {
		s.URL = "http://127.0.0.1"
		s.HTTPClient = &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
			if err := req.Context().Err(); err != nil {
				return nil, err
			}
			if req.Body == nil {
				req.Body = http.NoBody
			}
			req.RequestURI = req.URL.RequestURI()
			recorder := httptest.NewRecorder()
			handler.ServeHTTP(recorder, req)
			resp := recorder.Result()
			resp.Request = req
			return resp, nil
		})}
	}
	return s
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

func loadContract(t *testing.T) contractFile {
	t.Helper()
	_, self, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("contractmock: cannot locate package source")
	}
	path := filepath.Join(filepath.Dir(self), "..", "..", "docs", "contract.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("contractmock: read %s: %v", path, err)
	}
	var c contractFile
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatalf("contractmock: parse %s: %v", path, err)
	}
	if len(c.Schemas) == 0 {
		t.Fatalf("contractmock: %s names no schemas", path)
	}
	return c
}

// pathPattern turns "/v1/credentials/tasks/{id}" into an anchored regexp with
// one capture per template parameter, matched against the still-escaped path.
func pathPattern(template string) (*regexp.Regexp, []string) {
	var b strings.Builder
	var params []string
	b.WriteString("^")
	for _, seg := range strings.Split(strings.TrimPrefix(template, "/"), "/") {
		b.WriteString("/")
		if strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}") {
			params = append(params, strings.TrimSuffix(strings.TrimPrefix(seg, "{"), "}"))
			b.WriteString("([^/]+)")
			continue
		}
		b.WriteString(regexp.QuoteMeta(seg))
	}
	b.WriteString("$")
	return regexp.MustCompile(b.String()), params
}

// Requests returns a copy of the request log in arrival order.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.log))
	copy(out, s.log)
	return out
}

// OperationSequence returns the operationId of every logged request in arrival
// order. An unmatched request contributes "".
func (s *Server) OperationSequence() []string {
	reqs := s.Requests()
	out := make([]string, 0, len(reqs))
	for _, r := range reqs {
		out = append(out, r.OperationID)
	}
	return out
}

// Violations returns every contract violation the mock recorded, prefixed with
// the offending request.
func (s *Server) Violations() []string {
	var out []string
	for i, r := range s.Requests() {
		for _, v := range r.Violations {
			out = append(out, fmt.Sprintf("request %d (%s %s): %s", i, r.Method, r.RawTarget, v))
		}
	}
	return out
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	_ = r.Body.Close()

	entry := Request{
		Method:      r.Method,
		RawTarget:   r.RequestURI,
		EscapedPath: r.URL.EscapedPath(),
		Path:        r.URL.Path,
		Query:       r.URL.RawQuery,
		Header:      r.Header.Clone(),
		Body:        body,
		PathParams:  map[string]string{},
	}

	rt, params, matched := s.match(r)
	if matched {
		entry.OperationID = rt.operationID
		entry.PathParams = params
		if rt.operationID == "updateOrRotatePasswords" && s.hooks.BeforeSubmit != nil {
			s.hooks.BeforeSubmit()
		}
	}

	entry.Violations = append(entry.Violations, s.checkTarget(r)...)
	if !matched {
		entry.Violations = append(entry.Violations,
			fmt.Sprintf("no operation in docs/contract.json serves %s %s", r.Method, r.URL.EscapedPath()))
		s.record(entry)
		writeError(w, http.StatusNotFound, "ROUTE_NOT_IN_CONTRACT", "This route is not one the pinned contract names.")
		return
	}

	entry.Violations = append(entry.Violations, s.checkHeaders(r, rt)...)
	entry.Violations = append(entry.Violations, s.checkBody(rt, body)...)

	if len(entry.Violations) > 0 {
		s.record(entry)
		writeError(w, http.StatusBadRequest, "CONTRACT_VIOLATION", strings.Join(entry.Violations, "; "))
		return
	}

	s.record(entry)
	s.respond(w, rt, params)
}

func (s *Server) match(r *http.Request) (route, map[string]string, bool) {
	escaped := r.URL.EscapedPath()
	for _, rt := range s.routes {
		m := rt.pattern.FindStringSubmatch(escaped)
		if m == nil {
			continue
		}
		if rt.method != r.Method {
			continue
		}
		params := map[string]string{}
		for i, name := range rt.params {
			decoded, err := url.PathUnescape(m[i+1])
			if err != nil {
				decoded = m[i+1]
			}
			params[name] = decoded
		}
		return rt, params, true
	}
	return route{}, nil, false
}

func (s *Server) checkTarget(r *http.Request) []string {
	var v []string
	if strings.Contains(r.RequestURI, "?") {
		v = append(v, fmt.Sprintf("request target %q carries a query string or a bare %q; no operation in the contract takes a query parameter", r.RequestURI, "?"))
	}
	return v
}

func (s *Server) checkHeaders(r *http.Request, rt route) []string {
	var v []string

	auth := r.Header.Values("Authorization")
	switch {
	case len(auth) != 1:
		v = append(v, fmt.Sprintf("want exactly one Authorization header, got %d", len(auth)))
	case auth[0] != "Bearer "+AccessToken:
		v = append(v, "Authorization header is not \"Bearer <access_token>\" with the fixture access token")
	}

	accept := r.Header.Values("Accept")
	switch {
	case len(accept) != 1:
		v = append(v, fmt.Sprintf("want exactly one Accept header, got %d", len(accept)))
	case accept[0] != "application/json":
		v = append(v, fmt.Sprintf("Accept header is %q, want %q", accept[0], "application/json"))
	}

	ct := r.Header.Values("Content-Type")
	if rt.hasBody {
		switch {
		case len(ct) != 1:
			v = append(v, fmt.Sprintf("%s sends a body, so want exactly one Content-Type header, got %d", rt.operationID, len(ct)))
		case ct[0] != "application/json":
			v = append(v, fmt.Sprintf("Content-Type header is %q, want %q", ct[0], "application/json"))
		}
	} else if len(ct) != 0 {
		v = append(v, fmt.Sprintf("%s sends no body, so it must carry no Content-Type header, got %q", rt.operationID, strings.Join(ct, ", ")))
	}

	return v
}

func (s *Server) checkBody(rt route, body []byte) []string {
	if !rt.hasBody {
		if len(body) != 0 {
			return []string{fmt.Sprintf("%s takes no request body, got %d bytes", rt.operationID, len(body))}
		}
		return nil
	}
	if len(body) == 0 {
		return []string{fmt.Sprintf("%s requires a request body, got none", rt.operationID)}
	}

	dec := json.NewDecoder(strings.NewReader(string(body)))
	dec.UseNumber()
	var raw json.RawMessage
	if err := dec.Decode(&raw); err != nil {
		return []string{fmt.Sprintf("request body is not valid JSON: %v", err)}
	}
	if dec.More() {
		return []string{"request body carries trailing content after the JSON value"}
	}

	v := s.checkValue(rt.bodySchema, raw, "$")
	v = append(v, s.checkVocabularies(raw)...)
	return v
}

// readOnlyOnly is the set of member names that exist in this contract only as
// readOnly members of the task schemas. Seeing one in a request body is worth
// naming explicitly rather than reporting as a generic unknown member.
func (s *Server) readOnlyOnly() map[string]bool {
	requestMembers := map[string]bool{}
	for _, name := range []string{"CredentialsUpdateSpec", "ResourceCredentials", "BaseCredential", "AutoRotateCredentialPolicyInputSpec"} {
		for m := range s.contract.Schemas[name].Members {
			requestMembers[m] = true
		}
	}
	out := map[string]bool{}
	for _, name := range []string{"Task", "CredentialsTask", "CredentialsSubTask"} {
		for _, m := range s.contract.Schemas[name].ReadOnly {
			if !requestMembers[m] {
				out[m] = true
			}
		}
	}
	return out
}

func (s *Server) checkValue(schemaName string, raw json.RawMessage, path string) []string {
	sch, ok := s.contract.Schemas[schemaName]
	if !ok {
		return []string{fmt.Sprintf("%s: docs/contract.json declares no schema %q", path, schemaName)}
	}

	var obj map[string]json.RawMessage
	if err := json.Unmarshal(raw, &obj); err != nil {
		return []string{fmt.Sprintf("%s: want a JSON object for %s, got %s", path, schemaName, snippet(raw))}
	}

	var v []string
	readOnly := s.readOnlyOnly()

	present := map[string]bool{}
	names := make([]string, 0, len(obj))
	for name := range obj {
		names = append(names, name)
	}
	sort.Strings(names)

	for _, name := range names {
		val := obj[name]
		present[name] = true
		child := path + "." + name

		m, known := sch.Members[name]
		if !known {
			if readOnly[name] {
				v = append(v, fmt.Sprintf("%s: %q is a readOnly member of the task schemas and is never a member of %s, so it must never appear in a request", child, name, schemaName))
			} else {
				v = append(v, fmt.Sprintf("%s: %q is not a member of %s in the pinned 9.0.0.0 specification", child, name, schemaName))
			}
			continue
		}

		if string(val) == "null" {
			v = append(v, fmt.Sprintf("%s: an unset optional member is omitted, not sent as null", child))
			continue
		}

		switch m.Type {
		case "string":
			var str string
			if err := json.Unmarshal(val, &str); err != nil {
				v = append(v, fmt.Sprintf("%s: want a JSON string, got %s", child, snippet(val)))
				continue
			}
			if str == "" && s.contract.RequestRule.RejectBlankStringMembers {
				v = append(v, fmt.Sprintf("%s: an unset optional member is omitted, not sent as an empty string", child))
			}
		case "boolean":
			var b bool
			if err := json.Unmarshal(val, &b); err != nil {
				v = append(v, fmt.Sprintf("%s: want a JSON boolean, got %s", child, snippet(val)))
			}
		case "integer":
			var n json.Number
			if err := json.Unmarshal(val, &n); err != nil {
				v = append(v, fmt.Sprintf("%s: want a JSON number, got %s", child, snippet(val)))
				continue
			}
			if n.String() == "0" {
				v = append(v, fmt.Sprintf("%s: an unset optional number is omitted, not sent as 0", child))
			}
		case "array":
			var elems []json.RawMessage
			if err := json.Unmarshal(val, &elems); err != nil {
				v = append(v, fmt.Sprintf("%s: want a JSON array, got %s", child, snippet(val)))
				continue
			}
			if len(elems) == 0 {
				v = append(v, fmt.Sprintf("%s: want at least one element", child))
			}
			for i, e := range elems {
				v = append(v, s.checkValue(m.Items, e, fmt.Sprintf("%s[%d]", child, i))...)
			}
		case "object":
			v = append(v, s.checkValue(m.Items, val, child)...)
		default:
			v = append(v, fmt.Sprintf("%s: docs/contract.json gives member %q an unhandled type %q", child, name, m.Type))
		}
	}

	for _, req := range sch.Required {
		if !present[req] {
			v = append(v, fmt.Sprintf("%s: required member %q of %s is missing", path, req, schemaName))
		}
	}

	return v
}

// checkVocabularies enforces the enumerated values the pinned 9.0.0.0 document
// carries for operationType and resourceType. The 9.1.0.0 revision widens
// resourceType; those wider values are refused here.
func (s *Server) checkVocabularies(raw json.RawMessage) []string {
	var spec struct {
		OperationType string `json:"operationType"`
		Elements      []struct {
			ResourceType string `json:"resourceType"`
		} `json:"elements"`
	}
	if err := json.Unmarshal(raw, &spec); err != nil {
		return nil
	}

	var v []string
	if allowed, ok := s.contract.Vocabulary["operationType"]; ok && spec.OperationType != "" {
		if !contains(allowed.Allowed, spec.OperationType) {
			v = append(v, fmt.Sprintf("$.operationType: %q is not one of %v in the pinned 9.0.0.0 specification", spec.OperationType, allowed.Allowed))
		}
	}
	if allowed, ok := s.contract.Vocabulary["resourceType"]; ok {
		for i, e := range spec.Elements {
			if e.ResourceType == "" {
				continue
			}
			if !contains(allowed.Allowed, e.ResourceType) {
				v = append(v, fmt.Sprintf("$.elements[%d].resourceType: %q is not one of %v in the pinned 9.0.0.0 specification", i, e.ResourceType, allowed.Allowed))
			}
		}
	}
	return v
}

func contains(hay []string, needle string) bool {
	for _, h := range hay {
		if h == needle {
			return true
		}
	}
	return false
}

func snippet(raw json.RawMessage) string {
	s := string(raw)
	if len(s) > 60 {
		s = s[:60] + "..."
	}
	return s
}

func (s *Server) record(entry Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log = append(s.log, entry)
}

func (s *Server) respond(w http.ResponseWriter, rt route, params map[string]string) {
	switch rt.operationID {
	case "updateOrRotatePasswords":
		s.respondSubmit(w, rt)
	case "getCredentialsTask":
		s.respondPoll(w, rt, params["id"])
	case "cancelCredentialsTask":
		s.respondCancel(w, rt, params["id"])
	}
}

func (s *Server) respondSubmit(w http.ResponseWriter, rt route) {
	if s.scenario == ScenarioSubmitRejected {
		writeError(w, http.StatusBadRequest, SubmitRejectedErrorCode, SubmitRejectedMessage)
		return
	}
	status := "In Progress"
	if s.scenario == ScenarioAcceptedTerminal || s.scenario == ScenarioAcceptedTaskMissingName {
		status = "Successful"
	}
	// The 202 representation of updateOrRotatePasswords is Task, not
	// CredentialsTask, and the Task vocabulary carries mixed-case spellings.
	task := map[string]any{
		"id":                TaskID,
		"name":              "Rotate passwords",
		"type":              "CREDENTIALS_ROTATE",
		"status":            status,
		"creationTimestamp": "2026-03-04T09:12:44.180Z",
		"isCancellable":     true,
		"isRetryable":       false,
	}
	if s.scenario == ScenarioAcceptedTaskMissingName {
		delete(task, "name")
	}
	writeJSON(w, rt.successCode, task)
}

func (s *Server) respondPoll(w http.ResponseWriter, rt route, id string) {
	if id != TaskID {
		writeError(w, http.StatusBadRequest, "CREDENTIALS_TASK_NOT_FOUND",
			fmt.Sprintf("No credentials task with id %q.", id))
		return
	}
	if s.scenario == ScenarioPollRejected {
		writeError(w, http.StatusInternalServerError, PollRejectedErrorCode, PollRejectedMessage)
		return
	}

	s.mu.Lock()
	s.polls++
	n := s.polls
	s.mu.Unlock()

	switch s.scenario {
	case ScenarioAcceptedTerminal:
		s.mu.Lock()
		s.settled = "SUCCESSFUL"
		s.mu.Unlock()
		writeJSON(w, rt.successCode, credentialsTask("SUCCESSFUL", subTasksAllChanged(), nil))
		return
	case ScenarioUnknownStatus:
		writeJSON(w, rt.successCode, credentialsTask("PAUSED", nil, nil))
		return
	case ScenarioUserCancelled:
		s.mu.Lock()
		s.settled = "USER_CANCELLED"
		s.mu.Unlock()
		writeJSON(w, rt.successCode, credentialsTask("USER_CANCELLED", nil, nil))
		return
	case ScenarioMismatchedTaskID:
		task := credentialsTask("FAILED", nil, nil)
		task["id"] = "different-credentials-task"
		writeJSON(w, rt.successCode, task)
		return
	case ScenarioPendingForever:
		writeJSON(w, rt.successCode, credentialsTask("PENDING", nil, nil))
		return
	}

	if n == 1 {
		writeJSON(w, rt.successCode, credentialsTask("IN_PROGRESS", nil, nil))
		return
	}

	switch s.scenario {
	case ScenarioSucceeds:
		s.mu.Lock()
		s.settled = "SUCCESSFUL"
		s.mu.Unlock()
		writeJSON(w, rt.successCode, credentialsTask("SUCCESSFUL", subTasksAllChanged(), nil))
	case ScenarioPartial, ScenarioCancelRejected:
		s.mu.Lock()
		s.settled = "INCONSISTENT"
		s.mu.Unlock()
		writeJSON(w, rt.successCode, credentialsTask("INCONSISTENT", subTasksPartial(), []map[string]any{{
			"errorCode": TaskFailureErrorCode,
			"message":   TaskFailureMessage,
		}}))
	case ScenarioSuccessfulBlankPassword:
		s.mu.Lock()
		s.settled = "SUCCESSFUL"
		s.mu.Unlock()
		writeJSON(w, rt.successCode, credentialsTask("SUCCESSFUL", subTasksBlankPassword(), nil))
	}
}

func (s *Server) respondCancel(w http.ResponseWriter, rt route, id string) {
	if id != TaskID {
		writeError(w, http.StatusBadRequest, "CREDENTIALS_TASK_NOT_FOUND",
			fmt.Sprintf("No credentials task with id %q.", id))
		return
	}
	s.mu.Lock()
	settled := s.settled
	s.mu.Unlock()
	if settled != "FAILED" && settled != "INCONSISTENT" {
		writeError(w, http.StatusBadRequest, "CREDENTIALS_TASK_NOT_CANCELLABLE",
			fmt.Sprintf("Only a failed credentials task can be cancelled; task %q is %q.", id, settled))
		return
	}
	if s.scenario == ScenarioCancelRejected {
		writeError(w, http.StatusInternalServerError, CancelRejectedErrorCode, CancelRejectedMessage)
		return
	}
	writeJSON(w, rt.successCode, map[string]any{
		"id":                TaskID,
		"name":              "Cancel credentials task",
		"type":              "CREDENTIALS_CANCEL",
		"status":            "SUCCESSFUL",
		"creationTimestamp": "2026-03-04T09:14:02.006Z",
	})
}

func credentialsTask(status string, subTasks []map[string]any, errs []map[string]any) map[string]any {
	task := map[string]any{
		"id":                TaskID,
		"name":              "Rotate passwords",
		"type":              "ROTATE",
		"status":            status,
		"creationTimestamp": "2026-03-04T09:12:44.180Z",
		"isAutoRotate":      false,
	}
	if status != "IN_PROGRESS" && status != "PENDING" {
		task["completionTimestamp"] = "2026-03-04T09:13:51.442Z"
	}
	if subTasks != nil {
		task["subTasks"] = subTasks
	}
	if errs != nil {
		task["errors"] = errs
	}
	return task
}

// orchestrationSubTask carries neither a resourceName nor a username, so it
// correlates to no credential and must not become an Outcome.
func orchestrationSubTask() map[string]any {
	return map[string]any{
		"id":                "st-0",
		"name":              "Validate credentials",
		"description":       "Validate the supplied credentials against the target resources",
		"creationTimestamp": "2026-03-04T09:12:44.512Z",
		"status":            "SUCCESSFUL",
	}
}

func changedSubTask(id, resource, username, credType, oldPw, newPw string) map[string]any {
	return map[string]any{
		"id":                  id,
		"resourceName":        resource,
		"name":                "Rotate password",
		"description":         "Rotate the password of " + username + " on " + resource,
		"creationTimestamp":   "2026-03-04T09:12:45.001Z",
		"completionTimestamp": "2026-03-04T09:13:20.774Z",
		"status":              "SUCCESSFUL",
		"entityType":          "CREDENTIAL",
		"username":            username,
		"credentialType":      credType,
		"oldPassword":         oldPw,
		"newPassword":         newPw,
	}
}

func subTasksAllChanged() []map[string]any {
	return []map[string]any{
		orchestrationSubTask(),
		changedSubTask("st-1", ESXiHost, ESXiRootUser, "SSH", OldESXiRootPassword, NewESXiRootPassword),
		changedSubTask("st-2", ESXiHost, ESXiSvcUser, "SSH", OldESXiSvcPassword, NewESXiSvcPassword),
		changedSubTask("st-3", VCenter, VCenterUser, "SSO", OldVCenterPassword, NewVCenterPassword),
	}
}

func subTasksBlankPassword() []map[string]any {
	return []map[string]any{
		orchestrationSubTask(),
		changedSubTask("st-1", ESXiHost, ESXiRootUser, "SSH", OldESXiRootPassword, "   "),
		changedSubTask("st-2", ESXiHost, ESXiSvcUser, "SSH", OldESXiSvcPassword, NewESXiSvcPassword),
		changedSubTask("st-3", VCenter, VCenterUser, "SSO", OldVCenterPassword, NewVCenterPassword),
	}
}

func subTasksPartial() []map[string]any {
	return []map[string]any{
		orchestrationSubTask(),
		changedSubTask("st-1", ESXiHost, ESXiRootUser, "SSH", OldESXiRootPassword, NewESXiRootPassword),
		changedSubTask("st-2", ESXiHost, ESXiSvcUser, "SSH", OldESXiSvcPassword, NewESXiSvcPassword),
		{
			"id":                  "st-3",
			"resourceName":        VCenter,
			"name":                "Rotate password",
			"description":         "Rotate the password of " + VCenterUser + " on " + VCenter,
			"creationTimestamp":   "2026-03-04T09:12:45.001Z",
			"completionTimestamp": "2026-03-04T09:13:47.310Z",
			"status":              "FAILED",
			"entityType":          "CREDENTIAL",
			"username":            VCenterUser,
			"credentialType":      "SSO",
			// The appliance generated a replacement and recorded it before the
			// apply failed, so this subtask carries a newPassword that never
			// became live. Only a SUCCESSFUL subtask means the credential moved.
			"oldPassword": OldVCenterPassword,
			"newPassword": NewVCenterPassword,
			"errors": []map[string]any{{
				"errorCode": VCenterFailureErrorCode,
				"message":   VCenterFailureMessage,
			}},
		},
	}
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	raw, err := json.Marshal(body)
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
		"errorCode":      code,
		"errorType":      "VALIDATION_FAILED",
		"message":        message,
		"referenceToken": "R-9F31C0",
	})
}
