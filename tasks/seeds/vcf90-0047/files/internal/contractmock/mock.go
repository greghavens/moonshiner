// Package contractmock serves an in-memory stand-in for the vSphere Automation
// API of a VMware Cloud Foundation 9.0 vCenter Server.
//
// The route table is loaded from docs/contract.json at startup, so the mock can
// only ever answer the two operations that contract names:
// Esx.Settings.Clusters.Software_apply$Task and Cis.Tasks_get. Every other
// method, path or off-contract query is refused and recorded as a violation.
//
// Every request is appended to a mutex-guarded log that a test reads with
// Requests. Server implements http.RoundTripper and runs the same handler fully
// in memory, so verification does not depend on network access.
package contractmock

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"sync"
	"testing"
)

// Fixture values the client under test must thread through unchanged.
const (
	// SessionID is the value the api_key_auth header must carry.
	SessionID = "b3f0a4e2d19c47f8ae5610c7d2934bb1"

	// ClusterID is the ClusterComputeResource the tests remediate.
	ClusterID = "domain-c1006"

	// TaskID is the com.vmware.cis.task identifier the apply operation hands
	// back. Real vSphere task identifiers carry a colon-separated provider
	// suffix, so a client that treats one as a single path segment is exercised.
	TaskID = "52ae9b7c-1f04-4d7a-9c3e-6b0d8a2f5c11:com.vmware.esx.settings.clusters.software"

	// TaskService and TaskOperation appear in every Cis.Task.Info the mock serves.
	TaskService   = "com.vmware.esx.settings.clusters.software"
	TaskOperation = "apply"

	// DescriptionMessageID and DescriptionMessage are the task description.
	DescriptionMessageID = "com.vmware.esx.settings.clusters.software.apply.description"
	DescriptionMessage   = "Apply the desired software document to cluster domain-c1006."

	// ProgressMessageID and ProgressMessage appear in Cis.Task.Progress.
	ProgressMessageID = "com.vmware.esx.settings.clusters.software.apply.progress"
	ProgressMessage   = "Remediating hosts in cluster domain-c1006."

	// TaskErrorType, TaskErrorMessageID and TaskErrorMessage describe the
	// asynchronous failure served by ScenarioTaskFails.
	TaskErrorType      = "ERROR"
	TaskErrorMessageID = "com.vmware.esx.settings.clusters.software.apply.host_remediation_failed"
	TaskErrorMessage   = "Remediation failed on host esx-a07.vcf.local: the host could not enter maintenance mode within the configured timeout."

	// RejectedErrorType, RejectedMessageID and RejectedMessage describe the
	// synchronous 400 served by ScenarioApplyRejected.
	RejectedErrorType = "ALREADY_IN_DESIRED_STATE"
	RejectedMessageID = "com.vmware.esx.settings.clusters.software.already_in_desired_state"
	RejectedMessage   = "Cluster domain-c1006 is already at commit 41 of its desired software document."

	// UnknownStatus is the off-contract status served by ScenarioUnknownStatus.
	UnknownStatus = "WAITING"

	// TaskUser is the user the appliance attributes the task to.
	TaskUser = "VSPHERE.LOCAL\\Administrator"

	// TaskStartTime and TaskEndTime are the task timestamps.
	TaskStartTime = "2025-04-17T09:12:44.315Z"
	TaskEndTime   = "2025-04-17T09:41:02.880Z"

	// TotalWork is Cis.Task.Progress.total for every served task.
	TotalWork = 100
)

// ResultJSON is the operation-specific result the mock attaches to a settled
// task, unless the request asked for it to be excluded.
const ResultJSON = `{"commit":"42","hosts":{"host-42":{"status":"OK"}}}`

// Status sequences served by Cis.Tasks_get, one entry per successive call.
var (
	// SucceedStatuses walks back through RUNNING after BLOCKED, so a client that
	// stops at the first non-PENDING status or treats BLOCKED as terminal is caught.
	SucceedStatuses = []string{"PENDING", "RUNNING", "BLOCKED", "RUNNING", "SUCCEEDED"}
	// FailStatuses settles unsuccessfully.
	FailStatuses = []string{"PENDING", "RUNNING", "FAILED"}
	// UnknownStatuses ends on a status the specification does not define.
	UnknownStatuses = []string{"PENDING", UnknownStatus}
)

// Scenario selects what the appliance does with the remediation.
type Scenario int

const (
	// ScenarioSucceeds accepts the apply and settles the task SUCCEEDED after
	// walking every nonterminal status.
	ScenarioSucceeds Scenario = iota
	// ScenarioTaskFails accepts the apply with 202 and then settles the task
	// FAILED. Every HTTP call succeeds.
	ScenarioTaskFails
	// ScenarioUnknownStatus serves a status the contract does not define.
	ScenarioUnknownStatus
	// ScenarioApplyRejected refuses the apply with 400 AlreadyInDesiredState.
	ScenarioApplyRejected
	// ScenarioBlankTaskID answers the apply with 202 and an empty task id.
	ScenarioBlankTaskID
	// ScenarioNeverSettles keeps the task RUNNING forever.
	ScenarioNeverSettles
)

// Request is one logged inbound HTTP request.
type Request struct {
	// OperationID is the contract operationId the request matched, or "" when
	// the request matched no operation the contract names.
	OperationID string
	Method      string
	// RawTarget is r.RequestURI verbatim, so a stray query string or bare "?"
	// stays visible to the test.
	RawTarget string
	// Path is the percent-decoded request path.
	Path string
	// EscapedPath is the path exactly as it arrived on the wire.
	EscapedPath string
	// Query is r.URL.RawQuery verbatim.
	Query string
	// Header is a copy of the inbound headers.
	Header http.Header
	// Body is the full request body, empty when there was none.
	Body []byte
	// HadContentType records whether a Content-Type header arrived.
	HadContentType bool
	// Violation is set when the mock refused the request.
	Violation string
}

// Server is an in-memory vCenter stand-in and HTTP transport.
type Server struct {
	// URL is the simulated service root. The /api prefix from the
	// specification's server template is not part of it.
	URL string

	scenario Scenario
	routes   []route
	applySch schema
	sessHdr  string

	mu       sync.Mutex
	log      []Request
	taskGets int
}

type route struct {
	operationID   string
	method        string
	pattern       *regexp.Regexp
	requiredQuery map[string]string
}

type contractFile struct {
	Source struct {
		Commit           string `json:"commit"`
		Path             string `json:"path"`
		APIVersion       string `json:"api_version"`
		ServerPathPrefix string `json:"server_path_prefix"`
	} `json:"source"`
	Security struct {
		Name string `json:"name"`
		In   string `json:"in"`
	} `json:"security"`
	Operations []struct {
		OperationID   string            `json:"operationId"`
		Method        string            `json:"method"`
		Path          string            `json:"path"`
		RequiredQuery map[string]string `json:"required_query"`
		Request       *struct {
			Schema string `json:"schema"`
		} `json:"request"`
	} `json:"operations"`
	Schemas map[string]schema `json:"schemas"`
}

type schema struct {
	Required []string                   `json:"required"`
	Members  map[string]json.RawMessage `json:"members"`
}

// Start loads docs/contract.json and installs an in-memory transport on the
// standard library's default client for the lifetime of the test.
func Start(t *testing.T, scenario Scenario) *Server {
	t.Helper()

	contract := loadContract(t)
	if contract.Security.In != "header" || contract.Security.Name == "" {
		t.Fatalf("contract security scheme is not a header scheme: %+v", contract.Security)
	}

	s := &Server{
		scenario: scenario,
		sessHdr:  contract.Security.Name,
	}

	prefix := contract.Source.ServerPathPrefix
	for _, op := range contract.Operations {
		expr := "^" + regexp.QuoteMeta(prefix) + segmentPattern(op.Path) + "$"
		s.routes = append(s.routes, route{
			operationID:   op.OperationID,
			method:        op.Method,
			pattern:       regexp.MustCompile(expr),
			requiredQuery: op.RequiredQuery,
		})
		if op.Request != nil && op.Request.Schema != "" {
			if sch, ok := contract.Schemas[op.Request.Schema]; ok {
				s.applySch = sch
			}
		}
	}
	if len(s.routes) == 0 {
		t.Fatal("contract names no operations")
	}

	s.URL = "https://vc-a01.vcf.local"
	previousTransport := http.DefaultTransport
	http.DefaultTransport = s
	t.Cleanup(func() { http.DefaultTransport = previousTransport })
	return s
}

// RoundTrip executes the contract-backed HTTP handler in memory. RequestURI is
// populated as a real server would see it so the raw-target assertions remain
// meaningful.
func (s *Server) RoundTrip(req *http.Request) (*http.Response, error) {
	serverReq := req.Clone(req.Context())
	serverReq.RequestURI = req.URL.RequestURI()
	if serverReq.Body == nil {
		serverReq.Body = http.NoBody
	}
	recorder := httptest.NewRecorder()
	s.serve(recorder, serverReq)
	resp := recorder.Result()
	resp.Request = req
	return resp, nil
}

// segmentPattern turns "/cis/tasks/{task}" into "/cis/tasks/[^/]+".
func segmentPattern(p string) string {
	parts := strings.Split(p, "/")
	for i, seg := range parts {
		if strings.HasPrefix(seg, "{") && strings.HasSuffix(seg, "}") {
			parts[i] = "[^/]+"
		} else {
			parts[i] = regexp.QuoteMeta(seg)
		}
	}
	return strings.Join(parts, "/")
}

func loadContract(t *testing.T) contractFile {
	t.Helper()
	_, self, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate contractmock source directory")
	}
	path := filepath.Join(filepath.Dir(self), "..", "..", "docs", "contract.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("reading %s: %v", path, err)
	}
	var c contractFile
	if err := json.Unmarshal(raw, &c); err != nil {
		t.Fatalf("parsing %s: %v", path, err)
	}
	return c
}

// Requests returns a copy of the request log in arrival order.
func (s *Server) Requests() []Request {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Request, len(s.log))
	copy(out, s.log)
	return out
}

// OperationSequence returns the operationId of every logged request, using
// "<unmatched>" for requests that matched no contract operation.
func (s *Server) OperationSequence() []string {
	reqs := s.Requests()
	out := make([]string, 0, len(reqs))
	for _, r := range reqs {
		if r.OperationID == "" {
			out = append(out, "<unmatched>")
			continue
		}
		out = append(out, r.OperationID)
	}
	return out
}

func (s *Server) serve(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	_ = r.Body.Close()

	entry := Request{
		Method:         r.Method,
		RawTarget:      r.RequestURI,
		Path:           r.URL.Path,
		EscapedPath:    r.URL.EscapedPath(),
		Query:          r.URL.RawQuery,
		Header:         r.Header.Clone(),
		Body:           body,
		HadContentType: len(r.Header.Values("Content-Type")) > 0,
	}

	matched := s.match(r)
	if matched == nil {
		entry.Violation = fmt.Sprintf("no contract operation serves %s %s", r.Method, r.RequestURI)
		s.record(entry)
		writeError(w, http.StatusNotFound, "NOT_FOUND",
			"com.vmware.vapi.std.errors.not_found",
			fmt.Sprintf("This appliance serves only the operations named in docs/contract.json; %s %s is not one of them.", r.Method, r.URL.Path))
		return
	}
	entry.OperationID = matched.operationID

	if got := r.Header.Values(s.sessHdr); len(got) != 1 || got[0] != SessionID {
		entry.Violation = fmt.Sprintf("expected exactly one %s header carrying the fixture session id, got %q", s.sessHdr, got)
		s.record(entry)
		writeError(w, http.StatusUnauthorized, "UNAUTHENTICATED",
			"com.vmware.vapi.std.errors.unauthenticated",
			"The request did not carry a valid vmware-api-session-id header.")
		return
	}

	switch matched.operationID {
	case "Esx.Settings.Clusters.Software_apply$Task":
		s.serveApply(w, r, entry)
	case "Cis.Tasks_get":
		s.serveTaskGet(w, r, entry)
	default:
		entry.Violation = "contract names an operation the mock cannot serve: " + matched.operationID
		s.record(entry)
		writeError(w, http.StatusNotFound, "NOT_FOUND", "com.vmware.vapi.std.errors.not_found", "unserviceable operation")
	}
}

func (s *Server) match(r *http.Request) *route {
	for i := range s.routes {
		rt := &s.routes[i]
		if !rt.pattern.MatchString(r.URL.Path) {
			continue
		}
		if rt.method != r.Method {
			continue
		}
		q := r.URL.Query()
		ok := true
		for name, want := range rt.requiredQuery {
			if q.Get(name) != want {
				ok = false
				break
			}
		}
		if !ok {
			continue
		}
		return rt
	}
	return nil
}

func (s *Server) serveApply(w http.ResponseWriter, r *http.Request, entry Request) {
	if ct := r.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		entry.Violation = fmt.Sprintf("the apply request body is required and must be application/json, got %q", ct)
		s.record(entry)
		writeError(w, http.StatusBadRequest, "INVALID_ARGUMENT",
			"com.vmware.vapi.std.errors.invalid_argument", "Unsupported request content type.")
		return
	}

	var members map[string]json.RawMessage
	if err := json.Unmarshal(entry.Body, &members); err != nil || members == nil {
		entry.Violation = fmt.Sprintf("the apply body must be a JSON object matching Esx.Settings.Clusters.Software.ApplySpec, got %q", string(entry.Body))
		s.record(entry)
		writeError(w, http.StatusBadRequest, "INVALID_ARGUMENT",
			"com.vmware.vapi.std.errors.invalid_argument", "The apply specification is not a JSON object.")
		return
	}
	if unknown := unknownMembers(members, s.applySch); len(unknown) > 0 {
		entry.Violation = "the apply body carries members that are not in Esx.Settings.Clusters.Software.ApplySpec: " + strings.Join(unknown, ", ")
		s.record(entry)
		writeError(w, http.StatusBadRequest, "INVALID_ARGUMENT",
			"com.vmware.vapi.std.errors.invalid_argument", "Unexpected member in the apply specification.")
		return
	}

	if s.scenario == ScenarioApplyRejected {
		s.record(entry)
		writeError(w, http.StatusBadRequest, RejectedErrorType, RejectedMessageID, RejectedMessage)
		return
	}

	s.record(entry)
	id := TaskID
	if s.scenario == ScenarioBlankTaskID {
		id = ""
	}
	writeJSON(w, http.StatusAccepted, id)
}

func (s *Server) serveTaskGet(w http.ResponseWriter, r *http.Request, entry Request) {
	if len(entry.Body) > 0 {
		entry.Violation = "Cis.Tasks_get takes no request body"
		s.record(entry)
		writeError(w, http.StatusBadRequest, "INVALID_ARGUMENT",
			"com.vmware.vapi.std.errors.invalid_argument", "Cis.Tasks_get takes no request body.")
		return
	}

	q := r.URL.Query()
	if unknown := unknownQuery(q); len(unknown) > 0 {
		entry.Violation = "Cis.Tasks_get carries query parameters outside the exploded Cis.Tasks.GetSpec: " + strings.Join(unknown, ", ")
		s.record(entry)
		writeError(w, http.StatusBadRequest, "INVALID_ARGUMENT",
			"com.vmware.vapi.std.errors.invalid_argument", "Unexpected query parameter.")
		return
	}

	want := "/api/cis/tasks/" + TaskID
	if r.URL.Path != want {
		entry.Violation = fmt.Sprintf("polled task path %q, want %q", r.URL.Path, want)
		s.record(entry)
		writeError(w, http.StatusNotFound, "NOT_FOUND",
			"com.vmware.vapi.std.errors.not_found", "No task with that identifier exists.")
		return
	}

	s.mu.Lock()
	n := s.taskGets
	s.taskGets++
	s.log = append(s.log, entry)
	s.mu.Unlock()

	status := s.statusAt(n)
	excludeResult := q.Get("exclude_result") == "true"
	writeJSONRaw(w, http.StatusOK, s.taskInfo(status, excludeResult))
}

func (s *Server) statusAt(n int) string {
	var seq []string
	switch s.scenario {
	case ScenarioTaskFails:
		seq = FailStatuses
	case ScenarioUnknownStatus:
		seq = UnknownStatuses
	case ScenarioNeverSettles:
		return "RUNNING"
	default:
		seq = SucceedStatuses
	}
	if n >= len(seq) {
		n = len(seq) - 1
	}
	return seq[n]
}

// taskInfo renders a Cis.Task.Info for the given status. Members the
// specification marks optional are present only when they are relevant to that
// status, so a client cannot rely on a fixed member set.
func (s *Server) taskInfo(status string, excludeResult bool) []byte {
	info := map[string]any{
		"status":     status,
		"cancelable": status == "PENDING" || status == "RUNNING" || status == "BLOCKED",
		"service":    TaskService,
		"operation":  TaskOperation,
		"description": map[string]any{
			"id":              DescriptionMessageID,
			"default_message": DescriptionMessage,
			"args":            []string{ClusterID},
		},
		"user": TaskUser,
	}

	if status != "PENDING" {
		info["start_time"] = TaskStartTime
		completed := int64(40)
		switch status {
		case "BLOCKED":
			completed = 55
		case "SUCCEEDED", "FAILED":
			completed = TotalWork
		}
		info["progress"] = map[string]any{
			"total":     int64(TotalWork),
			"completed": completed,
			"message": map[string]any{
				"id":              ProgressMessageID,
				"default_message": ProgressMessage,
				"args":            []string{},
			},
		}
	}

	switch status {
	case "SUCCEEDED":
		info["end_time"] = TaskEndTime
		if !excludeResult {
			info["result"] = json.RawMessage(ResultJSON)
		}
	case "FAILED":
		info["end_time"] = TaskEndTime
		info["error"] = map[string]any{
			"error_type": TaskErrorType,
			"messages": []map[string]any{{
				"id":              TaskErrorMessageID,
				"default_message": TaskErrorMessage,
				"args":            []string{"esx-a07.vcf.local"},
			}},
		}
	}

	raw, err := json.Marshal(info)
	if err != nil {
		panic(err)
	}
	return raw
}

func (s *Server) record(entry Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.log = append(s.log, entry)
}

// unknownMembers lists body members the schema does not declare, sorted.
func unknownMembers(got map[string]json.RawMessage, sch schema) []string {
	if sch.Members == nil {
		return nil
	}
	var out []string
	for name := range got {
		if _, ok := sch.Members[name]; !ok {
			out = append(out, name)
		}
	}
	sort.Strings(out)
	return out
}

// unknownQuery lists Cis.Tasks_get query parameters outside the exploded
// Cis.Tasks.GetSpec member set, sorted.
func unknownQuery(q map[string][]string) []string {
	allowed := map[string]bool{"return_all": true, "exclude_result": true}
	var out []string
	for name := range q {
		if !allowed[name] {
			out = append(out, name)
		}
	}
	sort.Strings(out)
	return out
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	raw, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	writeJSONRaw(w, code, raw)
}

func writeJSONRaw(w http.ResponseWriter, code int, raw []byte) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_, _ = w.Write(raw)
}

func writeError(w http.ResponseWriter, code int, errorType, messageID, message string) {
	writeJSON(w, code, map[string]any{
		"error_type": errorType,
		"messages": []map[string]any{{
			"id":              messageID,
			"default_message": message,
			"args":            []string{},
		}},
	})
}
