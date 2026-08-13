// Package verification is the protected acceptance suite for the esxsoftware
// package. It drives the client against the contract-pinned in-memory mock and
// asserts both the outcome of the asynchronous remediation and the exact wire
// shape of every request the client made.
//
// No network endpoint is contacted.
package verification

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"
	"time"

	"example.com/vcf-esx-remediation/esxsoftware"
	"example.com/vcf-esx-remediation/internal/contractmock"
)

const (
	applyOp = "Esx.Settings.Clusters.Software_apply$Task"
	pollOp  = "Cis.Tasks_get"

	wantApplyPath  = "/api/esx/settings/clusters/" + contractmock.ClusterID + "/software"
	wantApplyQuery = "action=apply&vmw-task=true"
	wantPollPath   = "/api/cis/tasks/" + contractmock.TaskID

	spec900Commit = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
	spec910Commit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
	specPath      = "specifications/vsphere/openapi/automation/vcenter.yaml"

	fastPoll = time.Millisecond
)

func boolPtr(b bool) *bool { return &b }

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

func newClient(t *testing.T, srv *contractmock.Server) *esxsoftware.Client {
	t.Helper()
	c, err := esxsoftware.NewClient(srv.URL, contractmock.SessionID, nil)
	if err != nil {
		t.Fatalf("NewClient(%q, <session>, nil) returned error: %v", srv.URL, err)
	}
	if c == nil {
		t.Fatal("NewClient returned a nil client and a nil error")
	}
	return c
}

// repoFile reads a file relative to the module root.
func repoFile(t *testing.T, rel string) []byte {
	t.Helper()
	_, self, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate the verification source directory")
	}
	path := filepath.Join(filepath.Dir(self), "..", "..", rel)
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("reading %s: %v", rel, err)
	}
	return raw
}

// TestProvenance pins the contract to the 9.0.0.0 revision of the vCenter
// specification file and to the operationIds actually used.
func TestProvenance(t *testing.T) {
	var contract struct {
		Source struct {
			Repository string `json:"repository"`
			Commit     string `json:"commit"`
			Tag        string `json:"tag"`
			Path       string `json:"path"`
			APIVersion string `json:"api_version"`
		} `json:"source"`
		Security struct {
			Name string `json:"name"`
			In   string `json:"in"`
		} `json:"security"`
		Operations []struct {
			OperationID string `json:"operationId"`
			Method      string `json:"method"`
			Path        string `json:"path"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(repoFile(t, "docs/contract.json"), &contract); err != nil {
		t.Fatalf("docs/contract.json is not valid JSON: %v", err)
	}

	var sources struct {
		Repository string `json:"repository"`
		License    string `json:"repository_license"`
		Tag        string `json:"repository_tag"`
		Commit     string `json:"repository_commit_sha"`
		SpecPath   string `json:"spec_path"`
		Version    string `json:"spec_version"`
		Operations []struct {
			OperationID string `json:"operationId"`
			SpecPath    string `json:"spec_path"`
			Commit      string `json:"repository_commit_sha"`
			Source      string `json:"source"`
		} `json:"operations"`
	}
	if err := json.Unmarshal(repoFile(t, "docs/official_sources.json"), &sources); err != nil {
		t.Fatalf("docs/official_sources.json is not valid JSON: %v", err)
	}

	checks := []struct {
		name string
		got  string
		want string
	}{
		{"contract source commit", contract.Source.Commit, spec900Commit},
		{"contract source tag", contract.Source.Tag, "9.0.0.0"},
		{"contract source path", contract.Source.Path, specPath},
		{"contract api version", contract.Source.APIVersion, "9.0.0.0"},
		{"contract auth header", contract.Security.Name, "vmware-api-session-id"},
		{"contract auth location", contract.Security.In, "header"},
		{"sources repository", sources.Repository, "https://github.com/vmware/vcf-api-specs"},
		{"sources license", sources.License, "Apache-2.0"},
		{"sources tag", sources.Tag, "9.0.0.0"},
		{"sources commit sha", sources.Commit, spec900Commit},
		{"sources spec path", sources.SpecPath, specPath},
		{"sources spec version", sources.Version, "9.0.0.0"},
	}
	for _, c := range checks {
		if c.got != c.want {
			t.Errorf("%s = %q, want %q", c.name, c.got, c.want)
		}
	}

	wantOps := []string{applyOp, pollOp}
	var gotContractOps []string
	for _, op := range contract.Operations {
		gotContractOps = append(gotContractOps, op.OperationID)
	}
	if !reflect.DeepEqual(gotContractOps, wantOps) {
		t.Errorf("contract operationIds = %q, want %q", gotContractOps, wantOps)
	}

	var gotSourceOps []string
	for _, op := range sources.Operations {
		gotSourceOps = append(gotSourceOps, op.OperationID)
		if op.SpecPath != specPath {
			t.Errorf("official_sources operation %s spec_path = %q, want %q", op.OperationID, op.SpecPath, specPath)
		}
		if op.Commit != spec900Commit {
			t.Errorf("official_sources operation %s commit = %q, want %q", op.OperationID, op.Commit, spec900Commit)
		}
		if !strings.Contains(op.Source, spec900Commit) || !strings.Contains(op.Source, specPath) {
			t.Errorf("official_sources operation %s source %q does not anchor the pinned commit and spec path", op.OperationID, op.Source)
		}
	}
	if !reflect.DeepEqual(gotSourceOps, wantOps) {
		t.Errorf("official_sources operationIds = %q, want %q", gotSourceOps, wantOps)
	}

	// The 9.1.0.0 revision of the same file is not the contract. It may only be
	// named as an explicitly excluded source, never as the source of record.
	var sourcesDoc map[string]json.RawMessage
	if err := json.Unmarshal(repoFile(t, "docs/official_sources.json"), &sourcesDoc); err != nil {
		t.Fatalf("docs/official_sources.json is not a JSON object: %v", err)
	}
	for key, raw := range sourcesDoc {
		if key == "excluded_sources" {
			continue
		}
		if strings.Contains(string(raw), spec910Commit) || strings.Contains(string(raw), "9.1.0.0") {
			t.Errorf("official_sources %q names the 9.1.0.0 revision outside excluded_sources: %s", key, raw)
		}
	}
}

// bodyMembers decodes a request body into its top-level JSON members.
func bodyMembers(t *testing.T, raw []byte) map[string]string {
	t.Helper()
	var members map[string]json.RawMessage
	if err := json.Unmarshal(raw, &members); err != nil {
		t.Fatalf("request body %q is not a JSON object: %v", string(raw), err)
	}
	if members == nil {
		t.Fatalf("request body %q decoded to a JSON null, not an object", string(raw))
	}
	out := make(map[string]string, len(members))
	for k, v := range members {
		var buf bytes.Buffer
		if err := json.Compact(&buf, v); err != nil {
			t.Fatalf("member %q of %q is not valid JSON: %v", k, string(raw), err)
		}
		out[k] = buf.String()
	}
	return out
}

// TestApplyRequestBodyOmitsUnsetOptionalMembers is the wire-shape core: every
// member of Esx.Settings.Clusters.Software.ApplySpec is optional, so an unset
// member must be absent from the encoded object rather than encoded as null, an
// empty string, an empty array or false.
func TestApplyRequestBodyOmitsUnsetOptionalMembers(t *testing.T) {
	cases := []struct {
		name string
		spec esxsoftware.ApplySpec
		want map[string]string
	}{
		{
			name: "every member set",
			spec: esxsoftware.ApplySpec{
				Commit:     "42",
				Hosts:      []string{"host-42", "host-43"},
				AcceptEULA: boolPtr(true),
			},
			want: map[string]string{
				"commit":      `"42"`,
				"hosts":       `["host-42","host-43"]`,
				"accept_eula": `true`,
			},
		},
		{
			name: "nothing set sends an empty object",
			spec: esxsoftware.ApplySpec{},
			want: map[string]string{},
		},
		{
			name: "explicit false is a value and is sent",
			spec: esxsoftware.ApplySpec{AcceptEULA: boolPtr(false)},
			want: map[string]string{"accept_eula": `false`},
		},
		{
			name: "empty commit is unset",
			spec: esxsoftware.ApplySpec{Hosts: []string{"host-42"}},
			want: map[string]string{"hosts": `["host-42"]`},
		},
		{
			name: "empty host list is unset",
			spec: esxsoftware.ApplySpec{Commit: "41", Hosts: []string{}, AcceptEULA: boolPtr(true)},
			want: map[string]string{"commit": `"41"`, "accept_eula": `true`},
		},
		{
			name: "nil host list is unset",
			spec: esxsoftware.ApplySpec{Commit: "41", Hosts: nil},
			want: map[string]string{"commit": `"41"`},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := contractmock.Start(t, contractmock.ScenarioSucceeds)
			client := newClient(t, srv)

			taskID, err := client.ApplySoftware(context.Background(), contractmock.ClusterID, tc.spec)
			if err != nil {
				t.Fatalf("ApplySoftware returned error: %v", err)
			}
			if taskID != contractmock.TaskID {
				t.Errorf("ApplySoftware task id = %q, want %q", taskID, contractmock.TaskID)
			}

			reqs := srv.Requests()
			if len(reqs) != 1 {
				t.Fatalf("mock logged %d requests, want exactly 1: %+v", len(reqs), srv.OperationSequence())
			}
			req := reqs[0]
			if req.Violation != "" {
				t.Fatalf("the mock refused the apply request: %s", req.Violation)
			}
			got := bodyMembers(t, req.Body)
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("apply body members = %v, want %v (raw body %q)", got, tc.want, string(req.Body))
			}
			if len(tc.want) == 0 && strings.TrimSpace(string(req.Body)) != "{}" {
				t.Errorf("an apply spec with nothing set must encode as {}, got %q", string(req.Body))
			}
		})
	}
}

// TestApplyRequestTarget pins the request line of the asynchronous operation.
// Dropping vmw-task=true turns the call into a different operation that the
// contract does not name.
func TestApplyRequestTarget(t *testing.T) {
	cases := []struct {
		name    string
		baseURL func(srv *contractmock.Server) string
	}{
		{"bare service root", func(srv *contractmock.Server) string { return srv.URL }},
		{"service root with trailing slash", func(srv *contractmock.Server) string { return srv.URL + "/" }},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := contractmock.Start(t, contractmock.ScenarioSucceeds)
			client, err := esxsoftware.NewClient(tc.baseURL(srv), contractmock.SessionID, nil)
			if err != nil {
				t.Fatalf("NewClient(%q, <session>, nil) returned error: %v", tc.baseURL(srv), err)
			}
			if _, err := client.ApplySoftware(context.Background(), contractmock.ClusterID, esxsoftware.ApplySpec{}); err != nil {
				t.Fatalf("ApplySoftware returned error: %v", err)
			}

			reqs := srv.Requests()
			if len(reqs) != 1 {
				t.Fatalf("mock logged %d requests, want exactly 1", len(reqs))
			}
			req := reqs[0]
			if req.OperationID != applyOp {
				t.Fatalf("request matched operation %q, want %q (target %q, violation %q)",
					req.OperationID, applyOp, req.RawTarget, req.Violation)
			}
			if req.Method != http.MethodPost {
				t.Errorf("apply method = %q, want POST", req.Method)
			}
			if req.Path != wantApplyPath {
				t.Errorf("apply path = %q, want %q", req.Path, wantApplyPath)
			}
			if req.Query != wantApplyQuery {
				t.Errorf("apply query = %q, want %q", req.Query, wantApplyQuery)
			}
			if strings.Contains(req.Path, "//") {
				t.Errorf("apply path %q contains an empty segment", req.Path)
			}
			assertHeaders(t, req, true)
		})
	}
}

// TestPathParametersAreSingleEscapedSegments exercises delimiters that would
// change the route if a path parameter were concatenated without segment
// escaping. The contract mock uses fixed resource identifiers, so a recording
// transport is used here to inspect the requests before any network I/O.
func TestPathParametersAreSingleEscapedSegments(t *testing.T) {
	t.Run("cluster", func(t *testing.T) {
		var escapedPath string
		httpClient := &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
			escapedPath = req.URL.EscapedPath()
			return &http.Response{
				StatusCode: http.StatusAccepted,
				Header:     make(http.Header),
				Body:       io.NopCloser(strings.NewReader(`"task-42"`)),
				Request:    req,
			}, nil
		})}
		client, err := esxsoftware.NewClient("https://vc-a01.vcf.local", contractmock.SessionID, httpClient)
		if err != nil {
			t.Fatalf("NewClient returned error: %v", err)
		}
		if _, err := client.ApplySoftware(context.Background(), "domain/c1006 ?%", esxsoftware.ApplySpec{}); err != nil {
			t.Fatalf("ApplySoftware returned error: %v", err)
		}
		const want = "/api/esx/settings/clusters/domain%2Fc1006%20%3F%25/software"
		if escapedPath != want {
			t.Errorf("apply escaped path = %q, want %q", escapedPath, want)
		}
	})

	t.Run("task", func(t *testing.T) {
		var escapedPath string
		httpClient := &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
			escapedPath = req.URL.EscapedPath()
			return &http.Response{
				StatusCode: http.StatusOK,
				Header:     make(http.Header),
				Body: io.NopCloser(strings.NewReader(
					`{"status":"PENDING","cancelable":true,"service":"svc","operation":"get","description":{"id":"id","default_message":"pending","args":[]}}`,
				)),
				Request: req,
			}, nil
		})}
		client, err := esxsoftware.NewClient("https://vc-a01.vcf.local", contractmock.SessionID, httpClient)
		if err != nil {
			t.Fatalf("NewClient returned error: %v", err)
		}
		if _, err := client.GetTask(context.Background(), "task/provider?scope=cluster%id", esxsoftware.GetSpec{}); err != nil {
			t.Fatalf("GetTask returned error: %v", err)
		}
		const want = "/api/cis/tasks/task%2Fprovider%3Fscope=cluster%25id"
		if escapedPath != want {
			t.Errorf("poll escaped path = %q, want %q", escapedPath, want)
		}
	})
}

// assertHeaders checks header multiplicity and the absence of a scheme the
// contract does not use.
func assertHeaders(t *testing.T, req contractmock.Request, wantBody bool) {
	t.Helper()
	if got := req.Header.Values("vmware-api-session-id"); len(got) != 1 || got[0] != contractmock.SessionID {
		t.Errorf("vmware-api-session-id headers = %q, want exactly one carrying the session id", got)
	}
	if got := req.Header.Values("Accept"); len(got) != 1 || got[0] != "application/json" {
		t.Errorf("Accept headers = %q, want exactly one %q", got, "application/json")
	}
	if got := req.Header.Values("Authorization"); len(got) != 0 {
		t.Errorf("Authorization headers = %q, want none: the contract authenticates with vmware-api-session-id", got)
	}
	if wantBody {
		if got := req.Header.Values("Content-Type"); len(got) != 1 || got[0] != "application/json" {
			t.Errorf("Content-Type headers = %q, want exactly one %q", got, "application/json")
		}
		if len(req.Body) == 0 {
			t.Error("the apply request body is required by the contract but arrived empty")
		}
		return
	}
	if req.HadContentType {
		t.Errorf("a bodyless request must carry no Content-Type header, got %q", req.Header.Values("Content-Type"))
	}
	if len(req.Body) != 0 {
		t.Errorf("a bodyless request carried a body: %q", string(req.Body))
	}
}

// TestPollRequestShape pins Cis.Tasks_get, whose spec parameter is an exploded
// form object: each set member is its own query parameter, and an unset member
// contributes nothing.
func TestPollRequestShape(t *testing.T) {
	cases := []struct {
		name      string
		spec      esxsoftware.GetSpec
		wantQuery string
	}{
		{"nothing set sends no query string", esxsoftware.GetSpec{}, ""},
		{"exclude_result only", esxsoftware.GetSpec{ExcludeResult: boolPtr(true)}, "exclude_result=true"},
		{"return_all only", esxsoftware.GetSpec{ReturnAll: boolPtr(true)}, "return_all=true"},
		{
			"both set, sorted by parameter name",
			esxsoftware.GetSpec{ReturnAll: boolPtr(true), ExcludeResult: boolPtr(true)},
			"exclude_result=true&return_all=true",
		},
		{
			"explicit false is a value and is sent",
			esxsoftware.GetSpec{ReturnAll: boolPtr(false), ExcludeResult: boolPtr(false)},
			"exclude_result=false&return_all=false",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := contractmock.Start(t, contractmock.ScenarioSucceeds)
			client := newClient(t, srv)

			info, err := client.GetTask(context.Background(), contractmock.TaskID, tc.spec)
			if err != nil {
				t.Fatalf("GetTask returned error: %v", err)
			}
			if info == nil {
				t.Fatal("GetTask returned a nil TaskInfo and a nil error")
			}
			if info.Status != esxsoftware.StatusPending {
				t.Errorf("first poll status = %q, want %q", info.Status, esxsoftware.StatusPending)
			}

			reqs := srv.Requests()
			if len(reqs) != 1 {
				t.Fatalf("mock logged %d requests, want exactly 1", len(reqs))
			}
			req := reqs[0]
			if req.OperationID != pollOp {
				t.Fatalf("request matched operation %q, want %q (target %q, violation %q)",
					req.OperationID, pollOp, req.RawTarget, req.Violation)
			}
			if req.Method != http.MethodGet {
				t.Errorf("poll method = %q, want GET", req.Method)
			}
			if req.Path != wantPollPath {
				t.Errorf("poll path = %q, want %q", req.Path, wantPollPath)
			}
			if got := strings.Count(strings.TrimPrefix(req.EscapedPath, "/api/cis/tasks/"), "/"); got != 0 {
				t.Errorf("the task identifier must occupy exactly one path segment, escaped path = %q", req.EscapedPath)
			}
			if req.Query != tc.wantQuery {
				t.Errorf("poll query = %q, want %q", req.Query, tc.wantQuery)
			}
			if strings.HasSuffix(req.RawTarget, "?") {
				t.Errorf("poll target %q ends in a bare %q", req.RawTarget, "?")
			}
			assertHeaders(t, req, false)
		})
	}
}

type wantOutcome struct {
	taskID     string
	status     esxsoftware.Status
	sequence   []esxsoftware.Status
	polls      int
	succeeded  bool
	wantInfo   bool
	operations []string
}

// TestApplyAndAwaitPollsToTerminalState is the reason this package exists: the
// apply operation answers 202 with a task identifier, and the outcome is known
// only after the task settles.
func TestApplyAndAwaitPollsToTerminalState(t *testing.T) {
	cases := []struct {
		name     string
		scenario contractmock.Scenario
		want     wantOutcome
		check    func(t *testing.T, rep esxsoftware.Report, err error)
	}{
		{
			name:     "settles SUCCEEDED after every nonterminal status",
			scenario: contractmock.ScenarioSucceeds,
			want: wantOutcome{
				taskID: contractmock.TaskID,
				status: esxsoftware.StatusSucceeded,
				sequence: []esxsoftware.Status{
					esxsoftware.StatusPending, esxsoftware.StatusRunning,
					esxsoftware.StatusBlocked, esxsoftware.StatusRunning,
					esxsoftware.StatusSucceeded,
				},
				polls:      5,
				succeeded:  true,
				wantInfo:   true,
				operations: []string{applyOp, pollOp, pollOp, pollOp, pollOp, pollOp},
			},
			check: func(t *testing.T, rep esxsoftware.Report, err error) {
				if err != nil {
					t.Fatalf("ApplyAndAwait returned error: %v", err)
				}
				if rep.Info == nil {
					t.Fatal("Report.Info is nil for a settled task")
				}
				info := rep.Info
				if info.Service != contractmock.TaskService || info.Operation != contractmock.TaskOperation {
					t.Errorf("settled task service/operation = %q/%q, want %q/%q",
						info.Service, info.Operation, contractmock.TaskService, contractmock.TaskOperation)
				}
				if info.Description.ID != contractmock.DescriptionMessageID ||
					info.Description.DefaultMessage != contractmock.DescriptionMessage {
					t.Errorf("settled task description = %+v, want id %q", info.Description, contractmock.DescriptionMessageID)
				}
				if info.Cancelable {
					t.Error("a settled task is not cancelable")
				}
				if info.EndTime != contractmock.TaskEndTime {
					t.Errorf("settled task end_time = %q, want %q", info.EndTime, contractmock.TaskEndTime)
				}
				if info.Progress == nil {
					t.Fatal("the settled task carried progress but Info.Progress is nil")
				}
				if info.Progress.Total != contractmock.TotalWork || info.Progress.Completed != contractmock.TotalWork {
					t.Errorf("settled progress = %d/%d, want %d/%d",
						info.Progress.Completed, info.Progress.Total, contractmock.TotalWork, contractmock.TotalWork)
				}
				if info.Progress.Message.ID != contractmock.ProgressMessageID {
					t.Errorf("progress message id = %q, want %q", info.Progress.Message.ID, contractmock.ProgressMessageID)
				}
				if len(info.Result) == 0 {
					t.Error("the settled task carried a result but Info.Result is empty")
				}
				if len(info.Error) != 0 {
					t.Errorf("a SUCCEEDED task carries no error, got %s", info.Error)
				}
			},
		},
		{
			name:     "settles FAILED with every HTTP call succeeding",
			scenario: contractmock.ScenarioTaskFails,
			want: wantOutcome{
				taskID: contractmock.TaskID,
				status: esxsoftware.StatusFailed,
				sequence: []esxsoftware.Status{
					esxsoftware.StatusPending, esxsoftware.StatusRunning, esxsoftware.StatusFailed,
				},
				polls:      3,
				succeeded:  false,
				wantInfo:   true,
				operations: []string{applyOp, pollOp, pollOp, pollOp},
			},
			check: func(t *testing.T, rep esxsoftware.Report, err error) {
				var failed *esxsoftware.TaskFailedError
				if !errors.As(err, &failed) {
					t.Fatalf("ApplyAndAwait error = %v (%T), want a *esxsoftware.TaskFailedError", err, err)
				}
				var apiErr *esxsoftware.APIError
				if errors.As(err, &apiErr) {
					t.Error("an asynchronous failure is not an HTTP error; every call returned a success status")
				}
				if failed.TaskID != contractmock.TaskID {
					t.Errorf("TaskFailedError.TaskID = %q, want %q", failed.TaskID, contractmock.TaskID)
				}
				if failed.Status != esxsoftware.StatusFailed {
					t.Errorf("TaskFailedError.Status = %q, want %q", failed.Status, esxsoftware.StatusFailed)
				}
				if failed.ErrorType != contractmock.TaskErrorType {
					t.Errorf("TaskFailedError.ErrorType = %q, want %q", failed.ErrorType, contractmock.TaskErrorType)
				}
				if len(failed.Messages) != 1 {
					t.Fatalf("TaskFailedError.Messages = %+v, want exactly one message", failed.Messages)
				}
				msg := failed.Messages[0]
				if msg.ID != contractmock.TaskErrorMessageID || msg.DefaultMessage != contractmock.TaskErrorMessage {
					t.Errorf("TaskFailedError message = %+v, want id %q", msg, contractmock.TaskErrorMessageID)
				}
				if !reflect.DeepEqual(msg.Args, []string{"esx-a07.vcf.local"}) {
					t.Errorf("TaskFailedError message args = %q, want %q", msg.Args, []string{"esx-a07.vcf.local"})
				}
				if failed.Info == nil {
					t.Error("TaskFailedError.Info is nil; the settled task information is what explains the failure")
				}
			},
		},
		{
			name:     "an undefined status is a protocol error, not a terminal state",
			scenario: contractmock.ScenarioUnknownStatus,
			want: wantOutcome{
				taskID:     contractmock.TaskID,
				status:     esxsoftware.Status(contractmock.UnknownStatus),
				sequence:   []esxsoftware.Status{esxsoftware.StatusPending, esxsoftware.Status(contractmock.UnknownStatus)},
				polls:      2,
				succeeded:  false,
				wantInfo:   true,
				operations: []string{applyOp, pollOp, pollOp},
			},
			check: func(t *testing.T, rep esxsoftware.Report, err error) {
				var perr *esxsoftware.ProtocolError
				if !errors.As(err, &perr) {
					t.Fatalf("ApplyAndAwait error = %v (%T), want a *esxsoftware.ProtocolError", err, err)
				}
				if perr.OperationID != pollOp {
					t.Errorf("ProtocolError.OperationID = %q, want %q", perr.OperationID, pollOp)
				}
				if !strings.Contains(perr.Detail, contractmock.UnknownStatus) {
					t.Errorf("ProtocolError.Detail = %q, want it to name the offending status %q",
						perr.Detail, contractmock.UnknownStatus)
				}
			},
		},
		{
			name:     "a refused apply is never polled",
			scenario: contractmock.ScenarioApplyRejected,
			want: wantOutcome{
				taskID:     "",
				status:     "",
				sequence:   nil,
				polls:      0,
				succeeded:  false,
				wantInfo:   false,
				operations: []string{applyOp},
			},
			check: func(t *testing.T, rep esxsoftware.Report, err error) {
				var apiErr *esxsoftware.APIError
				if !errors.As(err, &apiErr) {
					t.Fatalf("ApplyAndAwait error = %v (%T), want a *esxsoftware.APIError", err, err)
				}
				if apiErr.OperationID != applyOp {
					t.Errorf("APIError.OperationID = %q, want %q", apiErr.OperationID, applyOp)
				}
				if apiErr.StatusCode != http.StatusBadRequest {
					t.Errorf("APIError.StatusCode = %d, want %d", apiErr.StatusCode, http.StatusBadRequest)
				}
				if apiErr.ErrorType != contractmock.RejectedErrorType {
					t.Errorf("APIError.ErrorType = %q, want %q", apiErr.ErrorType, contractmock.RejectedErrorType)
				}
				if len(apiErr.Messages) != 1 || apiErr.Messages[0].ID != contractmock.RejectedMessageID {
					t.Errorf("APIError.Messages = %+v, want exactly one with id %q", apiErr.Messages, contractmock.RejectedMessageID)
				}
				if len(apiErr.Messages) == 1 && apiErr.Messages[0].DefaultMessage != contractmock.RejectedMessage {
					t.Errorf("APIError message = %q, want %q", apiErr.Messages[0].DefaultMessage, contractmock.RejectedMessage)
				}
			},
		},
		{
			name:     "an accepted response with no task identifier is never polled",
			scenario: contractmock.ScenarioBlankTaskID,
			want: wantOutcome{
				taskID:     "",
				status:     "",
				sequence:   nil,
				polls:      0,
				succeeded:  false,
				wantInfo:   false,
				operations: []string{applyOp},
			},
			check: func(t *testing.T, rep esxsoftware.Report, err error) {
				var perr *esxsoftware.ProtocolError
				if !errors.As(err, &perr) {
					t.Fatalf("ApplyAndAwait error = %v (%T), want a *esxsoftware.ProtocolError", err, err)
				}
				if perr.OperationID != applyOp {
					t.Errorf("ProtocolError.OperationID = %q, want %q", perr.OperationID, applyOp)
				}
			},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := contractmock.Start(t, tc.scenario)
			client := newClient(t, srv)

			// Every case here reaches a terminal state or a hard error within a
			// handful of millisecond-spaced polls. The bound only keeps an
			// implementation that never converges from hanging the suite.
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()

			rep, err := client.ApplyAndAwait(ctx, contractmock.ClusterID,
				esxsoftware.ApplySpec{Commit: "42", AcceptEULA: boolPtr(true)},
				esxsoftware.PollOptions{Interval: fastPoll})

			assertOutcome(t, rep, tc.want)
			tc.check(t, rep, err)

			if got := srv.OperationSequence(); !reflect.DeepEqual(got, tc.want.operations) {
				t.Errorf("served operation sequence = %q, want %q", got, tc.want.operations)
			}
			for i, req := range srv.Requests() {
				if req.Violation != "" {
					t.Errorf("request %d (%s %s) was refused: %s", i, req.Method, req.RawTarget, req.Violation)
				}
			}
			if err != nil && strings.Contains(err.Error(), contractmock.SessionID) {
				t.Error("the error message leaks the session identifier")
			}
		})
	}
}

func assertOutcome(t *testing.T, rep esxsoftware.Report, want wantOutcome) {
	t.Helper()
	if rep.TaskID != want.taskID {
		t.Errorf("Report.TaskID = %q, want %q", rep.TaskID, want.taskID)
	}
	if rep.Status != want.status {
		t.Errorf("Report.Status = %q, want %q", rep.Status, want.status)
	}
	if rep.Polls != want.polls {
		t.Errorf("Report.Polls = %d, want %d", rep.Polls, want.polls)
	}
	if !sameStatuses(rep.StatusSequence, want.sequence) {
		t.Errorf("Report.StatusSequence = %s, want %s",
			formatStatuses(rep.StatusSequence), formatStatuses(want.sequence))
	}
	if rep.Succeeded != want.succeeded {
		t.Errorf("Report.Succeeded = %t, want %t", rep.Succeeded, want.succeeded)
	}
	if want.wantInfo && rep.Info == nil {
		t.Error("Report.Info is nil, want the task information from the last poll")
	}
	if !want.wantInfo && rep.Info != nil {
		t.Errorf("Report.Info = %+v, want nil: no poll completed", rep.Info)
	}
}

// sameStatuses compares two status sequences, treating a nil and an empty
// sequence as the same thing.
func sameStatuses(got, want []esxsoftware.Status) bool {
	if len(got) != len(want) {
		return false
	}
	for i := range want {
		if got[i] != want[i] {
			return false
		}
	}
	return true
}

// formatStatuses renders a status sequence, abbreviating a runaway one so an
// implementation that never converges does not bury the failure it caused.
func formatStatuses(s []esxsoftware.Status) string {
	const max = 12
	if len(s) <= max {
		return fmt.Sprintf("%q", s)
	}
	return fmt.Sprintf("%q ... and %d more (%d total)", s[:max], len(s)-max, len(s))
}

// TestApplyAndAwaitHonorsPollGetSpec checks that the poll options reach every
// Cis.Tasks_get call rather than only the first.
func TestApplyAndAwaitHonorsPollGetSpec(t *testing.T) {
	srv := contractmock.Start(t, contractmock.ScenarioSucceeds)
	client := newClient(t, srv)

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	rep, err := client.ApplyAndAwait(ctx, contractmock.ClusterID,
		esxsoftware.ApplySpec{},
		esxsoftware.PollOptions{
			Interval: fastPoll,
			GetSpec:  esxsoftware.GetSpec{ExcludeResult: boolPtr(true)},
		})
	if err != nil {
		t.Fatalf("ApplyAndAwait returned error: %v", err)
	}
	if !rep.Succeeded {
		t.Fatalf("Report.Succeeded = false, want true (report %+v)", rep)
	}

	polls := 0
	for _, req := range srv.Requests() {
		if req.OperationID != pollOp {
			continue
		}
		polls++
		if req.Query != "exclude_result=true" {
			t.Errorf("poll %d query = %q, want %q", polls, req.Query, "exclude_result=true")
		}
	}
	if polls != len(contractmock.SucceedStatuses) {
		t.Errorf("polled %d times, want %d", polls, len(contractmock.SucceedStatuses))
	}
	if rep.Info != nil && len(rep.Info.Result) != 0 {
		t.Errorf("exclude_result=true was honored by the appliance, so Info.Result must be empty, got %s", rep.Info.Result)
	}
}

// TestPollingStopsOnContextDeadline covers the task that never settles.
func TestPollingStopsOnContextDeadline(t *testing.T) {
	srv := contractmock.Start(t, contractmock.ScenarioNeverSettles)
	client := newClient(t, srv)

	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()

	start := time.Now()
	rep, err := client.ApplyAndAwait(ctx, contractmock.ClusterID, esxsoftware.ApplySpec{},
		esxsoftware.PollOptions{Interval: 0})
	elapsed := time.Since(start)

	if err == nil {
		t.Fatalf("ApplyAndAwait returned nil error for a task that never settles (report %+v)", rep)
	}
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Errorf("ApplyAndAwait error = %v, want it to wrap context.DeadlineExceeded", err)
	}
	if elapsed > 5*time.Second {
		t.Errorf("ApplyAndAwait took %v to notice the deadline", elapsed)
	}
	if rep.TaskID != contractmock.TaskID {
		t.Errorf("Report.TaskID = %q, want %q: the apply was accepted before the deadline", rep.TaskID, contractmock.TaskID)
	}
	if rep.Succeeded {
		t.Error("Report.Succeeded = true for a task that never settled")
	}
	if rep.Polls != 1 {
		t.Errorf("Report.Polls = %d, want exactly one immediate poll before DefaultPollInterval was interrupted", rep.Polls)
	}
	if rep.Status != esxsoftware.StatusRunning {
		t.Errorf("Report.Status = %q, want %q: the last status observed", rep.Status, esxsoftware.StatusRunning)
	}
}

// TestMockServesOnlyTheContractOperations proves the fixture is pinned to the
// contract: anything the contract does not name is refused and logged.
func TestMockServesOnlyTheContractOperations(t *testing.T) {
	srv := contractmock.Start(t, contractmock.ScenarioSucceeds)

	cases := []struct {
		name   string
		method string
		target string
	}{
		{"cancel is not in the contract", http.MethodPost, "/api/cis/tasks/" + contractmock.TaskID + "?action=cancel"},
		{"task list is not in the contract", http.MethodPost, "/api/cis/tasks?action=list"},
		{"a different cluster software action", http.MethodPost, "/api/esx/settings/clusters/" + contractmock.ClusterID + "/software?action=check&vmw-task=true"},
		{"the apply route without vmw-task", http.MethodPost, "/api/esx/settings/clusters/" + contractmock.ClusterID + "/software?action=apply"},
		{"an unrelated vcenter route", http.MethodGet, "/api/vcenter/vm"},
		{"the contract path without the server prefix", http.MethodGet, "/cis/tasks/" + contractmock.TaskID},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req, err := http.NewRequest(tc.method, srv.URL+tc.target, strings.NewReader("{}"))
			if err != nil {
				t.Fatalf("building probe request: %v", err)
			}
			req.Header.Set("vmware-api-session-id", contractmock.SessionID)
			req.Header.Set("Content-Type", "application/json")
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatalf("probe request: %v", err)
			}
			defer resp.Body.Close()
			if resp.StatusCode != http.StatusNotFound {
				t.Errorf("probe %s %s returned %d, want %d", tc.method, tc.target, resp.StatusCode, http.StatusNotFound)
			}
		})
	}

	reqs := srv.Requests()
	if len(reqs) != len(cases) {
		t.Fatalf("mock logged %d requests, want %d", len(reqs), len(cases))
	}
	for i, req := range reqs {
		if req.OperationID != "" {
			t.Errorf("probe %d matched operation %q, want no match", i, req.OperationID)
		}
		if req.Violation == "" {
			t.Errorf("probe %d (%s %s) was not recorded as a violation", i, req.Method, req.RawTarget)
		}
	}
}

// TestMockRejectsAMissingSession keeps the fixture honest about the security
// scheme the contract names.
func TestMockRejectsAMissingSession(t *testing.T) {
	srv := contractmock.Start(t, contractmock.ScenarioSucceeds)

	resp, err := http.Get(srv.URL + wantPollPath)
	if err != nil {
		t.Fatalf("probe request: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("unauthenticated poll returned %d, want %d", resp.StatusCode, http.StatusUnauthorized)
	}

	reqs := srv.Requests()
	if len(reqs) != 1 || reqs[0].OperationID != pollOp || reqs[0].Violation == "" {
		t.Fatalf("request log = %+v, want one matched-but-refused Cis.Tasks_get", reqs)
	}
}

// TestNewClientRejectsUnusableInput keeps bad configuration from reaching the wire.
func TestNewClientRejectsUnusableInput(t *testing.T) {
	cases := []struct {
		name      string
		baseURL   string
		sessionID string
	}{
		{"empty base URL", "", contractmock.SessionID},
		{"blank base URL", "   ", contractmock.SessionID},
		{"non-HTTP scheme", "ftp://vc-a01.vcf.local", contractmock.SessionID},
		{"no scheme", "vc-a01.vcf.local", contractmock.SessionID},
		{"empty session id", "https://vc-a01.vcf.local", ""},
		{"blank session id", "https://vc-a01.vcf.local", "  "},
		{"header-unsafe session id", "https://vc-a01.vcf.local", "abc\r\nX-Injected: 1"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			client, err := esxsoftware.NewClient(tc.baseURL, tc.sessionID, nil)
			if err == nil {
				t.Fatalf("NewClient(%q, %q, nil) returned client %v and a nil error", tc.baseURL, tc.sessionID, client)
			}
			if client != nil {
				t.Errorf("NewClient returned a non-nil client alongside an error")
			}
			if tc.sessionID != "" && strings.TrimSpace(tc.sessionID) != "" &&
				strings.Contains(err.Error(), strings.TrimSpace(tc.sessionID)) {
				t.Errorf("the error message leaks the session identifier: %v", err)
			}
		})
	}
}

// TestSingleShotOperationsUseTheCallersContext checks both single-shot
// operations honor cancellation without reaching the transport.
func TestSingleShotOperationsUseTheCallersContext(t *testing.T) {
	srv := contractmock.Start(t, contractmock.ScenarioSucceeds)
	client := newClient(t, srv)

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	if _, err := client.ApplySoftware(ctx, contractmock.ClusterID, esxsoftware.ApplySpec{}); err == nil {
		t.Fatal("ApplySoftware returned a nil error for an already-cancelled context")
	} else if !errors.Is(err, context.Canceled) {
		t.Errorf("ApplySoftware error = %v, want it to wrap context.Canceled", err)
	}
	if _, err := client.GetTask(ctx, contractmock.TaskID, esxsoftware.GetSpec{}); err == nil {
		t.Fatal("GetTask returned a nil error for an already-cancelled context")
	} else if !errors.Is(err, context.Canceled) {
		t.Errorf("GetTask error = %v, want it to wrap context.Canceled", err)
	}
	if got := len(srv.Requests()); got != 0 {
		t.Errorf("mock logged %d requests for cancelled contexts, want 0", got)
	}
}
