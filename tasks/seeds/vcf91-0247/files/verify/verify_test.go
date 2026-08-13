// Package verify holds the protected, table-driven verifier for the vSAN Data
// Protection snapshot client.
//
// It starts the contract-pinned loopback appliance on an ephemeral 127.0.0.1
// port, drives snapservice.Client against it and reads the appliance request
// log to check the exact wire shape of every request. No live VMware endpoint
// is contacted.
//
// This file is protected.
package verify

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"go/ast"
	"go/build"
	"go/parser"
	"go/token"
	"io"
	"net/http"
	"os"
	pathpkg "path"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"vsandp/internal/mockappliance"
	"vsandp/snapservice"
)

const (
	contractPath = "../docs/contract.json"
	sourcesPath  = "../docs/official_sources.json"

	createOp  = "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task"
	getTaskOp = "Snapservice.Tasks_get"
)

var (
	sessionToken    = "session-" + mustRuntimeHex(16)
	badSessionToken = "bad-session-" + mustRuntimeHex(16)
	clusterID       = "domain-" + mustRuntimeHex(8)
	pgID            = mustRuntimeHex(16)
	taskID          = mustRuntimeHex(16) + ":com.vmware.snapservice.task"
	unknownTaskID   = mustRuntimeHex(16) + ":com.vmware.snapservice.unknown-task"
	snapshotID      = "snapshot-" + mustRuntimeHex(12)
	failureMarker   = "APPLIANCE-ONLY-FAILURE-DETAIL-" + mustRuntimeHex(8)
	rejectMarker    = "APPLIANCE-ONLY-REJECTION-DETAIL-" + mustRuntimeHex(8)

	wantCreatePath = "/api/snapservice/clusters/" + clusterID + "/protection-groups/" + pgID + "/snapshots"
	wantTaskPath   = "/api/snapservice/tasks/" + taskID
)

// ---------------------------------------------------------------------------
// The contract must be the projection of the pinned specification.
// ---------------------------------------------------------------------------

func TestContractProjectsThePinnedSpecification(t *testing.T) {
	c, err := mockappliance.LoadContract(contractPath)
	if err != nil {
		t.Fatalf("load contract: %v", err)
	}

	t.Run("document", func(t *testing.T) {
		checks := []struct {
			name string
			got  any
			want any
		}{
			{"spec_title", c.SpecTitle, "Snapshot Appliance API"},
			{"spec_version", c.SpecVersion, "9.1.0.0"},
			{"base_path", c.BasePath, "/api"},
			{"source.repository", c.Source.Repository, "https://github.com/vmware/vcf-api-specs"},
			{"source.license", c.Source.License, "Apache-2.0"},
			{"source.spec_path", c.Source.SpecPath, "specifications/vsan-data-protection/vsan-data-protection-openapi.yaml"},
			{"auth.scheme", c.Auth.Scheme, "api_key_auth"},
			{"auth.type", c.Auth.Type, "apiKey"},
			{"auth.in", c.Auth.In, "header"},
			{"auth.name", c.Auth.Name, "vmware-api-session-id"},
			{"task_status.values", c.TaskStatus.Values, []string{"PENDING", "RUNNING", "BLOCKED", "SUCCEEDED", "FAILED"}},
			{"task_status.non_terminal", c.TaskStatus.NonTerminal, []string{"PENDING", "RUNNING", "BLOCKED"}},
			{"task_status.terminal", c.TaskStatus.Terminal, []string{"SUCCEEDED", "FAILED"}},
			{"task_status.success", c.TaskStatus.Success, "SUCCEEDED"},
			{"task_status.failure", c.TaskStatus.Failure, "FAILED"},
		}
		for _, tc := range checks {
			if !reflect.DeepEqual(tc.got, tc.want) {
				t.Errorf("contract %s = %v, want %v", tc.name, tc.got, tc.want)
			}
		}
		if !isHex40(c.Source.CommitSHA) {
			t.Errorf("contract source.commit_sha = %q, want a full 40-character lowercase commit sha", c.Source.CommitSHA)
		}
	})

	t.Run("operations", func(t *testing.T) {
		if len(c.Operations) != 2 {
			t.Fatalf("contract names %d operations, want exactly 2", len(c.Operations))
		}
		byID := map[string]mockappliance.Operation{}
		for _, op := range c.Operations {
			byID[op.OperationID] = op
		}

		create, ok := byID[createOp]
		if !ok {
			t.Fatalf("contract does not name operationId %q", createOp)
		}
		if create.Method != "POST" {
			t.Errorf("%s method = %q, want POST", createOp, create.Method)
		}
		if create.Path != "/snapservice/clusters/{cluster}/protection-groups/{pg}/snapshots" {
			t.Errorf("%s path = %q", createOp, create.Path)
		}
		if !reflect.DeepEqual(create.PathParams, []string{"cluster", "pg"}) {
			t.Errorf("%s path_params = %v", createOp, create.PathParams)
		}
		if !reflect.DeepEqual(create.Query, map[string]string{"vmw-task": "true"}) {
			t.Errorf("%s query = %v, want the specification's vmw-task=true", createOp, create.Query)
		}
		if create.SuccessStatus != 202 {
			t.Errorf("%s success_status = %d, want 202", createOp, create.SuccessStatus)
		}
		if !create.Asynchronous {
			t.Errorf("%s is not marked asynchronous", createOp)
		}
		if create.RequestBody == nil {
			t.Fatalf("%s declares no request body", createOp)
		}
		if !create.RequestBody.Required {
			t.Errorf("%s request body is not marked required", createOp)
		}
		if create.RequestBody.Schema != "Snapservice.Clusters.ProtectionGroups.Snapshots.CreateSpec" {
			t.Errorf("%s request body schema = %q", createOp, create.RequestBody.Schema)
		}
		if !reflect.DeepEqual(create.RequestBody.RequiredFields, []string{"name"}) {
			t.Errorf("%s required body fields = %v, want [name]", createOp, create.RequestBody.RequiredFields)
		}
		if !reflect.DeepEqual(create.RequestBody.OptionalFields, []string{"retention"}) {
			t.Errorf("%s optional body fields = %v, want [retention]", createOp, create.RequestBody.OptionalFields)
		}
		retention, ok := create.RequestBody.FieldSchemas["retention"]
		if !ok {
			t.Fatalf("%s does not describe the retention property", createOp)
		}
		if retention.Schema != "Snapservice.RetentionPeriod" {
			t.Errorf("retention schema = %q", retention.Schema)
		}
		if !reflect.DeepEqual(retention.RequiredFields, []string{"unit", "duration"}) {
			t.Errorf("retention required fields = %v", retention.RequiredFields)
		}
		if unit := retention.FieldSchemas["unit"]; !reflect.DeepEqual(unit.Enum,
			[]string{"MINUTE", "HOUR", "DAY", "WEEK", "MONTH", "YEAR"}) {
			t.Errorf("retention unit enum = %v", unit.Enum)
		}
		if dur := retention.FieldSchemas["duration"]; dur.Type != "integer" || dur.Format != "int64" {
			t.Errorf("retention duration schema = %+v", dur)
		}

		get, ok := byID[getTaskOp]
		if !ok {
			t.Fatalf("contract does not name operationId %q", getTaskOp)
		}
		if get.Method != "GET" {
			t.Errorf("%s method = %q, want GET", getTaskOp, get.Method)
		}
		if get.Path != "/snapservice/tasks/{task}" {
			t.Errorf("%s path = %q", getTaskOp, get.Path)
		}
		if !reflect.DeepEqual(get.PathParams, []string{"task"}) {
			t.Errorf("%s path_params = %v", getTaskOp, get.PathParams)
		}
		if len(get.Query) != 0 {
			t.Errorf("%s declares query fields %v, want none", getTaskOp, get.Query)
		}
		if get.RequestBody != nil {
			t.Errorf("%s declares a request body", getTaskOp)
		}
		if get.SuccessStatus != 200 {
			t.Errorf("%s success_status = %d, want 200", getTaskOp, get.SuccessStatus)
		}
		if get.SuccessBody.Schema != "Snapservice.Tasks.Info" {
			t.Errorf("%s success body schema = %q", getTaskOp, get.SuccessBody.Schema)
		}
		wantRequired := []string{"cancelable", "description", "operation", "service", "status"}
		gotRequired := append([]string(nil), get.SuccessBody.RequiredFields...)
		sort.Strings(gotRequired)
		if !reflect.DeepEqual(gotRequired, wantRequired) {
			t.Errorf("%s success body required fields = %v, want %v", getTaskOp, gotRequired, wantRequired)
		}
	})
}

func TestOfficialSourcesRecordThePinnedSpecification(t *testing.T) {
	raw, err := os.ReadFile(sourcesPath)
	if err != nil {
		t.Fatalf("read %s: %v", sourcesPath, err)
	}
	var doc struct {
		Sources []struct {
			Kind         string   `json:"kind"`
			Repository   string   `json:"repository"`
			License      string   `json:"license"`
			CommitSHA    string   `json:"commit_sha"`
			SpecPath     string   `json:"spec_path"`
			SpecVersion  string   `json:"spec_version"`
			RawURL       string   `json:"raw_url"`
			OperationIDs []string `json:"operation_ids"`
		} `json:"sources"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("parse %s: %v", sourcesPath, err)
	}
	if len(doc.Sources) != 1 {
		t.Fatalf("%s records %d sources, want exactly 1", sourcesPath, len(doc.Sources))
	}
	s := doc.Sources[0]

	contract, err := mockappliance.LoadContract(contractPath)
	if err != nil {
		t.Fatalf("load contract: %v", err)
	}

	checks := []struct {
		name string
		got  any
		want any
	}{
		{"kind", s.Kind, "openapi-specification"},
		{"repository", s.Repository, "https://github.com/vmware/vcf-api-specs"},
		{"license", s.License, "Apache-2.0"},
		{"spec_path", s.SpecPath, "specifications/vsan-data-protection/vsan-data-protection-openapi.yaml"},
		{"spec_version", s.SpecVersion, "9.1.0.0"},
		{"commit_sha", s.CommitSHA, contract.Source.CommitSHA},
	}
	for _, tc := range checks {
		if !reflect.DeepEqual(tc.got, tc.want) {
			t.Errorf("official source %s = %v, want %v", tc.name, tc.got, tc.want)
		}
	}
	if !isHex40(s.CommitSHA) {
		t.Fatalf("official source commit_sha = %q, want a full 40-character lowercase commit sha", s.CommitSHA)
	}
	if !strings.HasPrefix(s.RawURL, "https://") || !strings.Contains(s.RawURL, s.CommitSHA) || !strings.HasSuffix(s.RawURL, s.SpecPath) {
		t.Errorf("official source raw_url = %q, want an https URL pinned to the recorded commit and spec path", s.RawURL)
	}

	gotOps := append([]string(nil), s.OperationIDs...)
	sort.Strings(gotOps)
	var contractOps []string
	for _, op := range contract.Operations {
		contractOps = append(contractOps, op.OperationID)
	}
	sort.Strings(contractOps)
	if !reflect.DeepEqual(gotOps, contractOps) {
		t.Errorf("official source operation_ids = %v, want the contract's %v", gotOps, contractOps)
	}
	want := []string{createOp, getTaskOp}
	sort.Strings(want)
	if !reflect.DeepEqual(gotOps, want) {
		t.Errorf("official source operation_ids = %v, want %v", gotOps, want)
	}
}

func TestImplementationUsesOnlyTheStandardLibraryAndDoesNotShellOut(t *testing.T) {
	const implementation = "../snapservice/client.go"
	parsed, err := parser.ParseFile(token.NewFileSet(), implementation, nil, parser.SkipObjectResolution)
	if err != nil {
		t.Fatalf("parse %s: %v", implementation, err)
	}

	imports := map[string]string{}
	for _, spec := range parsed.Imports {
		importPath, err := strconv.Unquote(spec.Path.Value)
		if err != nil {
			t.Fatalf("unquote import %s: %v", spec.Path.Value, err)
		}
		pkg, err := build.Default.Import(importPath, "../snapservice", build.FindOnly)
		if err != nil || !pkg.Goroot {
			t.Errorf("%s imports %q, want only Go standard-library packages", implementation, importPath)
		}
		if importPath == "os/exec" {
			t.Errorf("%s imports os/exec; the client must not shell out", implementation)
		}
		name := pathpkg.Base(importPath)
		if spec.Name != nil {
			name = spec.Name.Name
		}
		imports[name] = importPath
	}

	ast.Inspect(parsed, func(node ast.Node) bool {
		sel, ok := node.(*ast.SelectorExpr)
		if !ok {
			return true
		}
		ident, ok := sel.X.(*ast.Ident)
		if !ok {
			return true
		}
		importPath := imports[ident.Name]
		if importPath == "os" && sel.Sel.Name == "StartProcess" {
			t.Errorf("%s calls os.StartProcess; the client must not shell out", implementation)
		}
		if importPath == "syscall" && (sel.Sel.Name == "Exec" || sel.Sel.Name == "ForkExec" || sel.Sel.Name == "StartProcess") {
			t.Errorf("%s calls syscall.%s; the client must not shell out", implementation, sel.Sel.Name)
		}
		return true
	})
}

// ---------------------------------------------------------------------------
// Request wire shape.
// ---------------------------------------------------------------------------

func TestCreateRequestWireShape(t *testing.T) {
	tests := []struct {
		name      string
		retention *snapservice.RetentionPeriod
		wantBody  map[string]any
	}{
		{
			name:      "unset retention is omitted, not sent empty",
			retention: nil,
			wantBody:  map[string]any{"name": "nightly-2026-05-13"},
		},
		{
			name:      "retention in days",
			retention: &snapservice.RetentionPeriod{Unit: snapservice.Day, Duration: 7},
			wantBody: map[string]any{
				"name":      "nightly-2026-05-13",
				"retention": map[string]any{"unit": "DAY", "duration": float64(7)},
			},
		},
		{
			name:      "retention in hours",
			retention: &snapservice.RetentionPeriod{Unit: snapservice.Hour, Duration: 36},
			wantBody: map[string]any{
				"name":      "nightly-2026-05-13",
				"retention": map[string]any{"unit": "HOUR", "duration": float64(36)},
			},
		},
		{
			name:      "retention in minutes",
			retention: &snapservice.RetentionPeriod{Unit: snapservice.Minute, Duration: 90},
			wantBody: map[string]any{
				"name":      "nightly-2026-05-13",
				"retention": map[string]any{"unit": "MINUTE", "duration": float64(90)},
			},
		},
		{
			name:      "retention in months",
			retention: &snapservice.RetentionPeriod{Unit: snapservice.Month, Duration: 3},
			wantBody: map[string]any{
				"name":      "nightly-2026-05-13",
				"retention": map[string]any{"unit": "MONTH", "duration": float64(3)},
			},
		},
		{
			name:      "retention in years",
			retention: &snapservice.RetentionPeriod{Unit: snapservice.Year, Duration: 1},
			wantBody: map[string]any{
				"name":      "nightly-2026-05-13",
				"retention": map[string]any{"unit": "YEAR", "duration": float64(1)},
			},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			app := newAppliance(t, mockappliance.Config{
				Tasks: map[string]mockappliance.TaskScript{
					"nightly-2026-05-13": {
						ID:     taskID,
						States: []string{"PENDING", "RUNNING", "SUCCEEDED"},
						Result: map[string]any{"snapshot": snapshotID},
					},
				},
			})
			client := newClient(t, app)

			res, err := client.CreateProtectionGroupSnapshot(context.Background(), snapservice.SnapshotRequest{
				Cluster:         clusterID,
				ProtectionGroup: pgID,
				Name:            "nightly-2026-05-13",
				Retention:       tc.retention,
			})
			if err != nil {
				t.Fatalf("CreateProtectionGroupSnapshot: %v", err)
			}
			if res == nil {
				t.Fatal("CreateProtectionGroupSnapshot returned a nil result and a nil error")
			}

			log := app.Requests()
			if len(log) != 4 {
				t.Fatalf("appliance saw %d requests, want 1 create and 3 polls: %s", len(log), summarize(log))
			}

			create := log[0]
			assertMatched(t, create, createOp)
			if create.Method != "POST" {
				t.Errorf("create method = %q, want POST", create.Method)
			}
			if create.Path != wantCreatePath {
				t.Errorf("create path = %q, want %q", create.Path, wantCreatePath)
			}
			if create.RawPath != create.Path {
				t.Errorf("create path arrived as %q but decodes to %q; path parameters must be percent-encoded for a path segment", create.RawPath, create.Path)
			}
			if create.RawQuery != "vmw-task=true" {
				t.Errorf("create query string = %q, want exactly %q", create.RawQuery, "vmw-task=true")
			}
			assertHeader(t, create, "vmware-api-session-id", sessionToken)
			assertHeader(t, create, "Content-Type", "application/json")
			assertHeader(t, create, "Accept", "application/json")
			assertNoHeader(t, create, "Authorization")

			var body map[string]any
			if err := json.Unmarshal(create.Body, &body); err != nil {
				t.Fatalf("create body %q is not a JSON object: %v", create.Body, err)
			}
			if !reflect.DeepEqual(body, tc.wantBody) {
				t.Errorf("create body = %v, want %v", body, tc.wantBody)
			}
			if tc.retention == nil {
				if _, present := body["retention"]; present {
					t.Errorf("unset retention was sent as %#v; an unset optional property must be omitted from the body", body["retention"])
				}
			}
			for k, v := range body {
				if v == nil {
					t.Errorf("create body property %q was sent as null", k)
				}
			}

			for i, poll := range log[1:] {
				assertMatched(t, poll, getTaskOp)
				if poll.Method != "GET" {
					t.Errorf("poll %d method = %q, want GET", i+1, poll.Method)
				}
				if poll.Path != wantTaskPath {
					t.Errorf("poll %d path = %q, want %q", i+1, poll.Path, wantTaskPath)
				}
				if poll.RawPath != poll.Path {
					t.Errorf("poll %d path arrived as %q but decodes to %q; the task identifier is a JSON string in the 202 body and must be decoded before it is put in the path", i+1, poll.RawPath, poll.Path)
				}
				if poll.RawQuery != "" {
					t.Errorf("poll %d sent query string %q, want none", i+1, poll.RawQuery)
				}
				if len(poll.Body) != 0 {
					t.Errorf("poll %d sent a body %q, want none", i+1, poll.Body)
				}
				assertHeader(t, poll, "vmware-api-session-id", sessionToken)
				assertHeader(t, poll, "Accept", "application/json")
				assertNoHeader(t, poll, "Content-Type")
				assertNoHeader(t, poll, "Authorization")
			}

			if res.TaskID != taskID {
				t.Errorf("TaskID = %q, want %q", res.TaskID, taskID)
			}
			if res.Status != "SUCCEEDED" {
				t.Errorf("Status = %q, want SUCCEEDED", res.Status)
			}
			if res.Polls != 3 {
				t.Errorf("Polls = %d, want 3", res.Polls)
			}
			if !jsonEqual(res.Result, map[string]any{"snapshot": snapshotID}) {
				t.Errorf("Result = %#v, want the appliance's task result", res.Result)
			}
			if res.StartTime == "" || res.EndTime == "" {
				t.Errorf("StartTime = %q, EndTime = %q, want both taken from the terminal task info", res.StartTime, res.EndTime)
			}
		})
	}
}

func TestPathSegmentsAndJSONTaskIdentifierAreDecodedAndEscaped(t *testing.T) {
	const (
		cluster = "domain c/100%?x#y"
		pg      = "pg \"west\"/blue"
		id      = "task \"a/b\"?100%#"
	)
	app := newAppliance(t, mockappliance.Config{
		Tasks: map[string]mockappliance.TaskScript{
			"escaped-identifiers": {ID: id, States: []string{"SUCCEEDED"}},
		},
	})
	client := newClient(t, app)

	res, err := client.CreateProtectionGroupSnapshot(context.Background(), snapservice.SnapshotRequest{
		Cluster: cluster, ProtectionGroup: pg, Name: "escaped-identifiers",
	})
	if err != nil {
		t.Fatalf("CreateProtectionGroupSnapshot: %v", err)
	}
	if res.TaskID != id {
		t.Errorf("TaskID = %q, want the decoded JSON string %q", res.TaskID, id)
	}

	log := app.Requests()
	if len(log) != 2 {
		t.Fatalf("appliance saw %d requests, want one create and one poll: %s", len(log), summarize(log))
	}
	assertMatched(t, log[0], createOp)
	assertMatched(t, log[1], getTaskOp)
	if got, want := log[0].RawPath,
		"/api/snapservice/clusters/domain%20c%2F100%25%3Fx%23y/protection-groups/pg%20%22west%22%2Fblue/snapshots"; got != want {
		t.Errorf("escaped create path = %q, want %q", got, want)
	}
	if got, want := log[1].RawPath,
		"/api/snapservice/tasks/task%20%22a%2Fb%22%3F100%25%23"; got != want {
		t.Errorf("escaped task path = %q, want %q", got, want)
	}
}

// ---------------------------------------------------------------------------
// The operation is asynchronous and must be polled to a terminal state.
// ---------------------------------------------------------------------------

func TestTaskIsPolledToATerminalState(t *testing.T) {
	tests := []struct {
		name       string
		states     []string
		wantPolls  int
		wantStatus string
		wantFailed bool
	}{
		{"terminal on the first poll", []string{"SUCCEEDED"}, 1, "SUCCEEDED", false},
		{"pending then done", []string{"PENDING", "SUCCEEDED"}, 2, "SUCCEEDED", false},
		{"pending, running, done", []string{"PENDING", "RUNNING", "SUCCEEDED"}, 3, "SUCCEEDED", false},
		{"blocked in the middle", []string{"PENDING", "BLOCKED", "RUNNING", "SUCCEEDED"}, 4, "SUCCEEDED", false},
		{"stops at the first terminal status", []string{"PENDING", "SUCCEEDED", "RUNNING", "SUCCEEDED"}, 2, "SUCCEEDED", false},
		{"failed is terminal too", []string{"RUNNING", "FAILED"}, 2, "FAILED", true},
		{"failed on the first poll", []string{"FAILED"}, 1, "FAILED", true},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			app := newAppliance(t, mockappliance.Config{
				Tasks: map[string]mockappliance.TaskScript{
					"nightly-2026-05-13": {
						ID:             taskID,
						States:         tc.states,
						Result:         map[string]any{"snapshot": snapshotID},
						FailureMessage: failureMarker,
					},
				},
			})
			client := newClient(t, app)

			res, err := client.CreateProtectionGroupSnapshot(context.Background(), basicRequest())

			polls := app.RequestsFor(getTaskOp)
			if len(polls) != tc.wantPolls {
				t.Fatalf("appliance saw %d %s requests, want %d: %s", len(polls), getTaskOp, tc.wantPolls, summarize(app.Requests()))
			}
			if last := app.Requests()[len(app.Requests())-1]; last.OperationID != getTaskOp {
				t.Errorf("the last request was %q, want the terminal poll; nothing may follow a terminal status", last.OperationID)
			}

			if tc.wantFailed {
				if err == nil {
					t.Fatalf("a %s task returned no error (result %#v)", tc.wantStatus, res)
				}
				var apiErr *snapservice.Error
				if !errors.As(err, &apiErr) {
					t.Fatalf("error %v is not a *snapservice.Error", err)
				}
				if !strings.Contains(err.Error(), "FAILED") {
					t.Errorf("error %q does not name the terminal status", err)
				}
				if !strings.Contains(err.Error(), taskID) {
					t.Errorf("error %q does not name the task identifier", err)
				}
				if strings.Contains(err.Error(), failureMarker) {
					t.Errorf("error %q leaks the appliance's localizable message", err)
				}
				if strings.Contains(err.Error(), sessionToken) {
					t.Errorf("error %q leaks the session token", err)
				}
				return
			}

			if err != nil {
				t.Fatalf("CreateProtectionGroupSnapshot: %v", err)
			}
			if res.Status != tc.wantStatus {
				t.Errorf("Status = %q, want %q", res.Status, tc.wantStatus)
			}
			if res.Polls != tc.wantPolls {
				t.Errorf("Polls = %d, want %d", res.Polls, tc.wantPolls)
			}
			if res.TaskID != taskID {
				t.Errorf("TaskID = %q, want %q", res.TaskID, taskID)
			}
		})
	}
}

func TestOmittedOptionalTaskPropertiesRemainUnset(t *testing.T) {
	app := newAppliance(t, mockappliance.Config{
		Tasks: map[string]mockappliance.TaskScript{
			"nightly-2026-05-13": {
				ID: taskID, States: []string{"SUCCEEDED"}, OmitTerminalTimes: true,
			},
		},
	})
	client := newClient(t, app)

	res, err := client.CreateProtectionGroupSnapshot(context.Background(), basicRequest())
	if err != nil {
		t.Fatalf("CreateProtectionGroupSnapshot: %v", err)
	}
	if res.Result != nil {
		t.Errorf("Result = %#v, want nil when the appliance omits it", res.Result)
	}
	if res.StartTime != "" || res.EndTime != "" {
		t.Errorf("StartTime = %q, EndTime = %q, want empty strings when omitted", res.StartTime, res.EndTime)
	}
}

func TestMaxPollsIsHonoured(t *testing.T) {
	app := newAppliance(t, mockappliance.Config{
		Tasks: map[string]mockappliance.TaskScript{
			"nightly-2026-05-13": {ID: taskID, States: []string{"PENDING"}},
		},
	})
	client := newClient(t, app)
	client.MaxPolls = 4

	res, err := client.CreateProtectionGroupSnapshot(context.Background(), basicRequest())
	if err == nil {
		t.Fatalf("a task that never reaches a terminal status returned no error (result %#v)", res)
	}
	var apiErr *snapservice.Error
	if !errors.As(err, &apiErr) {
		t.Fatalf("error %v is not a *snapservice.Error", err)
	}
	if got := len(app.RequestsFor(getTaskOp)); got != 4 {
		t.Errorf("appliance saw %d polls, want exactly MaxPolls (4)", got)
	}
}

func TestUnusableSuccessBodiesFailImmediately(t *testing.T) {
	bodyMarker := "UNUSABLE-BODY-MARKER-" + mustRuntimeHex(8)
	tests := []struct {
		name       string
		configure  func(*mockappliance.Config)
		wantOp     string
		wantStatus int
		wantPolls  int
	}{
		{
			name: "create body is not JSON",
			configure: func(cfg *mockappliance.Config) {
				cfg.CreateRawBody = []byte(bodyMarker)
			},
			wantOp: createOp, wantStatus: http.StatusAccepted,
		},
		{
			name: "create body has an empty task identifier",
			configure: func(cfg *mockappliance.Config) {
				cfg.CreateRawBody = []byte(`""`)
			},
			wantOp: createOp, wantStatus: http.StatusAccepted,
		},
		{
			name: "task body is not JSON",
			configure: func(cfg *mockappliance.Config) {
				cfg.TaskRawBody = []byte(bodyMarker)
			},
			wantOp: getTaskOp, wantStatus: http.StatusOK, wantPolls: 1,
		},
		{
			name: "task body has no status",
			configure: func(cfg *mockappliance.Config) {
				cfg.TaskRawBody = []byte(`{"result":"` + bodyMarker + `"}`)
			},
			wantOp: getTaskOp, wantStatus: http.StatusOK, wantPolls: 1,
		},
		{
			name: "task status is outside the contract enumeration",
			configure: func(cfg *mockappliance.Config) {
				cfg.TaskRawBody = []byte(`{"status":"PAUSED","result":"` + bodyMarker + `"}`)
			},
			wantOp: getTaskOp, wantStatus: http.StatusOK, wantPolls: 1,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			cfg := mockappliance.Config{Tasks: map[string]mockappliance.TaskScript{
				"nightly-2026-05-13": {ID: taskID, States: []string{"SUCCEEDED"}},
			}}
			tc.configure(&cfg)
			app := newAppliance(t, cfg)
			client := newClient(t, app)

			res, err := client.CreateProtectionGroupSnapshot(context.Background(), basicRequest())
			if err == nil {
				t.Fatalf("unusable response returned no error (result %#v)", res)
			}
			var apiErr *snapservice.Error
			if !errors.As(err, &apiErr) {
				t.Fatalf("error %v is not a *snapservice.Error", err)
			}
			if apiErr.Op != tc.wantOp || apiErr.Status != tc.wantStatus {
				t.Errorf("Error = {Op:%q Status:%d}, want {Op:%q Status:%d}",
					apiErr.Op, apiErr.Status, tc.wantOp, tc.wantStatus)
			}
			if strings.Contains(err.Error(), bodyMarker) {
				t.Errorf("error %q leaks the unusable response body", err)
			}
			if got := len(app.RequestsFor(getTaskOp)); got != tc.wantPolls {
				t.Errorf("appliance saw %d polls, want %d", got, tc.wantPolls)
			}
		})
	}
}

func TestPollingHonoursContextCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	app := newAppliance(t, mockappliance.Config{
		Tasks: map[string]mockappliance.TaskScript{
			"nightly-2026-05-13": {ID: taskID, States: []string{"PENDING"}},
		},
	})
	client := newClient(t, app)
	client.PollInterval = 10 * time.Second
	client.MaxPolls = 3
	client.HTTPClient.Transport = cancelAtPollEOFTransport{
		base: http.DefaultTransport, cancel: cancel,
	}

	started := time.Now()
	res, err := client.CreateProtectionGroupSnapshot(ctx, basicRequest())
	elapsed := time.Since(started)
	if err == nil {
		t.Fatalf("a cancelled poll returned no error (result %#v)", res)
	}
	if elapsed >= 5*time.Second {
		t.Errorf("the call took %v to return after cancellation with a %v poll interval; the wait between polls must observe the context rather than sleep it out",
			elapsed, client.PollInterval)
	}
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("error %v does not unwrap to context.Canceled", err)
	}
	if got := len(app.RequestsFor(getTaskOp)); got != 1 {
		t.Errorf("appliance saw %d polls after cancellation, want 1", got)
	}
}

func TestExpiredContextSendsNothing(t *testing.T) {
	app := newAppliance(t, mockappliance.Config{
		Tasks: map[string]mockappliance.TaskScript{
			"nightly-2026-05-13": {ID: taskID, States: []string{"SUCCEEDED"}},
		},
	})
	client := newClient(t, app)
	done := make(chan struct{})
	close(done)
	ctx := fixedErrorContext{done: done, err: context.DeadlineExceeded}

	res, err := client.CreateProtectionGroupSnapshot(ctx, basicRequest())
	if err == nil {
		t.Fatalf("an expired context returned no error (result %#v)", res)
	}
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("error %v does not unwrap to context.DeadlineExceeded", err)
	}
	if log := app.Requests(); len(log) != 0 {
		t.Errorf("appliance saw %d requests for an already expired context, want none: %s", len(log), summarize(log))
	}
}

// ---------------------------------------------------------------------------
// Failures the appliance is responsible for.
// ---------------------------------------------------------------------------

func TestApplianceFailuresSurface(t *testing.T) {
	tests := []struct {
		name         string
		createStatus int
		badSession   bool
		unknownTask  bool
		wantStatus   int
		wantOp       string
		wantPolls    int
	}{
		{name: "create rejected", createStatus: http.StatusBadRequest, wantStatus: 400, wantOp: createOp},
		{name: "cluster or group not found", createStatus: http.StatusNotFound, wantStatus: 404, wantOp: createOp},
		{name: "appliance unavailable", createStatus: http.StatusServiceUnavailable, wantStatus: 503, wantOp: createOp},
		{name: "session token rejected", badSession: true, wantStatus: 401, wantOp: createOp},
		{name: "task identifier not found", unknownTask: true, wantStatus: 404, wantOp: getTaskOp, wantPolls: 1},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			cfg := mockappliance.Config{
				CreateStatus:     tc.createStatus,
				CreateBodyMarker: rejectMarker,
				Tasks: map[string]mockappliance.TaskScript{
					"nightly-2026-05-13": {ID: taskID, States: []string{"SUCCEEDED"}},
				},
			}
			if tc.unknownTask {
				cfg.CreateTaskIDOverride = unknownTaskID
			}
			app := newAppliance(t, cfg)

			token := sessionToken
			if tc.badSession {
				token = badSessionToken
			}
			client, err := snapservice.NewClient(app.URL(), token)
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}
			client.PollInterval = time.Millisecond
			client.MaxPolls = 8

			res, err := client.CreateProtectionGroupSnapshot(context.Background(), basicRequest())
			if err == nil {
				t.Fatalf("a %d response returned no error (result %#v)", tc.wantStatus, res)
			}
			var apiErr *snapservice.Error
			if !errors.As(err, &apiErr) {
				t.Fatalf("error %v is not a *snapservice.Error", err)
			}
			if apiErr.Status != tc.wantStatus {
				t.Errorf("Error.Status = %d, want %d", apiErr.Status, tc.wantStatus)
			}
			if apiErr.Op != tc.wantOp {
				t.Errorf("Error.Op = %q, want %q", apiErr.Op, tc.wantOp)
			}
			if strings.Contains(err.Error(), rejectMarker) {
				t.Errorf("error %q leaks the appliance response body", err)
			}
			if strings.Contains(err.Error(), token) {
				t.Errorf("error %q leaks the session token", err)
			}
			if got := len(app.RequestsFor(getTaskOp)); got != tc.wantPolls {
				t.Errorf("appliance saw %d polls, want %d", got, tc.wantPolls)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// Local validation never reaches the appliance.
// ---------------------------------------------------------------------------

func TestNewClientRejectsAnUnusableServiceRoot(t *testing.T) {
	tests := []struct {
		name        string
		serviceRoot string
		sessionID   string
	}{
		{"empty service root", "", sessionToken},
		{"blank service root", "   ", sessionToken},
		{"no scheme", "appliance.vsphere.local", sessionToken},
		{"unsupported scheme", "ftp://appliance.vsphere.local", sessionToken},
		{"no host", "https://", sessionToken},
		{"credentials in the origin", "https://admin:secret@appliance.vsphere.local", sessionToken},
		{"non-root path", "https://appliance.vsphere.local/api", sessionToken},
		{"query in the origin", "https://appliance.vsphere.local/?probe=1", sessionToken},
		{"empty query in the origin", "https://appliance.vsphere.local/?", sessionToken},
		{"fragment in the origin", "https://appliance.vsphere.local/#top", sessionToken},
		{"empty fragment in the origin", "https://appliance.vsphere.local/#", sessionToken},
		{"empty session token", "https://appliance.vsphere.local", ""},
		{"blank session token", "https://appliance.vsphere.local", "  "},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			client, err := snapservice.NewClient(tc.serviceRoot, tc.sessionID)
			if err == nil {
				t.Fatalf("NewClient(%q, %q) returned a client (%#v), want an error", tc.serviceRoot, tc.sessionID, client)
			}
			if !errors.Is(err, snapservice.ErrInvalidRequest) {
				t.Errorf("error %v does not unwrap to ErrInvalidRequest", err)
			}
		})
	}
}

func TestNewClientAppliesDefaults(t *testing.T) {
	client, err := snapservice.NewClient("https://appliance.vsphere.local/", sessionToken)
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if client.PollInterval != snapservice.DefaultPollInterval {
		t.Errorf("PollInterval = %v, want %v", client.PollInterval, snapservice.DefaultPollInterval)
	}
	if client.MaxPolls != snapservice.DefaultMaxPolls {
		t.Errorf("MaxPolls = %d, want %d", client.MaxPolls, snapservice.DefaultMaxPolls)
	}
	if client.HTTPClient == nil {
		t.Fatal("HTTPClient is nil, want a client with a timeout")
	}
	if client.HTTPClient.Timeout != snapservice.DefaultTimeout {
		t.Errorf("HTTPClient.Timeout = %v, want %v", client.HTTPClient.Timeout, snapservice.DefaultTimeout)
	}
	if client.SessionID != sessionToken {
		t.Errorf("SessionID = %q, want %q", client.SessionID, sessionToken)
	}
	if client.ServiceRoot != "https://appliance.vsphere.local" {
		t.Errorf("ServiceRoot = %q, want the normalized origin without a path", client.ServiceRoot)
	}
}

func TestInvalidRequestSendsNothing(t *testing.T) {
	tests := []struct {
		name string
		req  snapservice.SnapshotRequest
	}{
		{"no cluster", snapservice.SnapshotRequest{ProtectionGroup: pgID, Name: "n"}},
		{"blank cluster", snapservice.SnapshotRequest{Cluster: " ", ProtectionGroup: pgID, Name: "n"}},
		{"no protection group", snapservice.SnapshotRequest{Cluster: clusterID, Name: "n"}},
		{"no name", snapservice.SnapshotRequest{Cluster: clusterID, ProtectionGroup: pgID}},
		{"blank name", snapservice.SnapshotRequest{Cluster: clusterID, ProtectionGroup: pgID, Name: "  "}},
		{
			"retention without a unit",
			snapservice.SnapshotRequest{Cluster: clusterID, ProtectionGroup: pgID, Name: "n",
				Retention: &snapservice.RetentionPeriod{Duration: 7}},
		},
		{
			"retention unit outside the enumeration",
			snapservice.SnapshotRequest{Cluster: clusterID, ProtectionGroup: pgID, Name: "n",
				Retention: &snapservice.RetentionPeriod{Unit: "FORTNIGHT", Duration: 7}},
		},
		{
			"retention unit in the wrong case",
			snapservice.SnapshotRequest{Cluster: clusterID, ProtectionGroup: pgID, Name: "n",
				Retention: &snapservice.RetentionPeriod{Unit: "day", Duration: 7}},
		},
		{
			"retention with a zero duration",
			snapservice.SnapshotRequest{Cluster: clusterID, ProtectionGroup: pgID, Name: "n",
				Retention: &snapservice.RetentionPeriod{Unit: snapservice.Day, Duration: 0}},
		},
		{
			"retention with a negative duration",
			snapservice.SnapshotRequest{Cluster: clusterID, ProtectionGroup: pgID, Name: "n",
				Retention: &snapservice.RetentionPeriod{Unit: snapservice.Day, Duration: -1}},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			app := newAppliance(t, mockappliance.Config{
				Tasks: map[string]mockappliance.TaskScript{
					"n": {ID: taskID, States: []string{"SUCCEEDED"}},
				},
			})
			client := newClient(t, app)

			res, err := client.CreateProtectionGroupSnapshot(context.Background(), tc.req)
			if err == nil {
				t.Fatalf("invalid request returned a result %#v, want an error", res)
			}
			if !errors.Is(err, snapservice.ErrInvalidRequest) {
				t.Errorf("error %v does not unwrap to ErrInvalidRequest", err)
			}
			if log := app.Requests(); len(log) != 0 {
				t.Errorf("appliance saw %d requests, want none: %s", len(log), summarize(log))
			}
		})
	}
}

// ---------------------------------------------------------------------------
// The appliance is pinned to the contract.
// ---------------------------------------------------------------------------

func TestApplianceServesOnlyContractOperations(t *testing.T) {
	app := newAppliance(t, mockappliance.Config{
		Tasks: map[string]mockappliance.TaskScript{
			"nightly-2026-05-13": {ID: taskID, States: []string{"SUCCEEDED"}},
		},
	})

	tests := []struct {
		name   string
		method string
		target string
	}{
		{"an operation outside the contract", "POST", "/api/snapservice/sessions"},
		{"another operation outside the contract", "GET", "/api/snapservice/clusters/" + clusterID + "/protection-groups"},
		{"the create path without the contract's query field", "POST", wantCreatePath},
		{"the create path with the wrong query value", "POST", wantCreatePath + "?vmw-task=false"},
		{"the task path without the base path", "GET", "/snapservice/tasks/" + taskID},
		{"the create path with the wrong method", "GET", wantCreatePath + "?vmw-task=true"},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			req, err := http.NewRequest(tc.method, app.URL()+tc.target, nil)
			if err != nil {
				t.Fatalf("build request: %v", err)
			}
			req.Header.Set("vmware-api-session-id", sessionToken)
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				t.Fatalf("request: %v", err)
			}
			defer resp.Body.Close()
			if resp.StatusCode != http.StatusNotFound {
				t.Errorf("%s %s answered %d, want 404: the appliance serves only the operations the contract names",
					tc.method, tc.target, resp.StatusCode)
			}
		})
	}

	for _, r := range app.Requests() {
		if r.Matched {
			t.Errorf("%s %s matched operationId %q, want no match", r.Method, r.Path, r.OperationID)
		}
	}
}

// ---------------------------------------------------------------------------
// Concurrency.
// ---------------------------------------------------------------------------

func TestConcurrentSnapshotsAreRaceFree(t *testing.T) {
	const workers = 8

	tasks := map[string]mockappliance.TaskScript{}
	for i := 0; i < workers; i++ {
		tasks[fmt.Sprintf("nightly-%02d", i)] = mockappliance.TaskScript{
			ID:     fmt.Sprintf("task-%02d:com.vmware.snapservice.task", i),
			States: []string{"PENDING", "RUNNING", "SUCCEEDED"},
			Result: map[string]any{"snapshot": fmt.Sprintf("snap-%02d", i)},
		}
	}
	app := newAppliance(t, mockappliance.Config{Tasks: tasks})
	client := newClient(t, app)

	var wg sync.WaitGroup
	errs := make([]error, workers)
	results := make([]*snapservice.TaskResult, workers)
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			results[i], errs[i] = client.CreateProtectionGroupSnapshot(context.Background(), snapservice.SnapshotRequest{
				Cluster:         clusterID,
				ProtectionGroup: pgID,
				Name:            fmt.Sprintf("nightly-%02d", i),
				Retention:       &snapservice.RetentionPeriod{Unit: snapservice.Week, Duration: int64(i + 1)},
			})
		}(i)
	}
	wg.Wait()

	for i := 0; i < workers; i++ {
		if errs[i] != nil {
			t.Fatalf("worker %d: %v", i, errs[i])
		}
		want := fmt.Sprintf("task-%02d:com.vmware.snapservice.task", i)
		if results[i].TaskID != want {
			t.Errorf("worker %d TaskID = %q, want %q", i, results[i].TaskID, want)
		}
		if results[i].Status != "SUCCEEDED" || results[i].Polls != 3 {
			t.Errorf("worker %d = status %q after %d polls, want SUCCEEDED after 3", i, results[i].Status, results[i].Polls)
		}
		if !jsonEqual(results[i].Result, map[string]any{"snapshot": fmt.Sprintf("snap-%02d", i)}) {
			t.Errorf("worker %d Result = %#v", i, results[i].Result)
		}
	}
	if got := len(app.RequestsFor(createOp)); got != workers {
		t.Errorf("appliance saw %d create requests, want %d", got, workers)
	}
	if got := len(app.RequestsFor(getTaskOp)); got != workers*3 {
		t.Errorf("appliance saw %d polls, want %d", got, workers*3)
	}
}

// ---------------------------------------------------------------------------
// Helpers.
// ---------------------------------------------------------------------------

func mustRuntimeHex(bytes int) string {
	raw := make([]byte, bytes)
	if _, err := rand.Read(raw); err != nil {
		panic(fmt.Sprintf("generate protected verifier fixture: %v", err))
	}
	return hex.EncodeToString(raw)
}

// cancelAtPollEOFTransport cancels the context only after the first poll body
// has been completely delivered. That deterministically puts cancellation
// between polls, where the client's interval wait must observe it.
type cancelAtPollEOFTransport struct {
	base   http.RoundTripper
	cancel context.CancelFunc
}

type fixedErrorContext struct {
	done <-chan struct{}
	err  error
}

func (c fixedErrorContext) Deadline() (time.Time, bool) { return time.Time{}, true }
func (c fixedErrorContext) Done() <-chan struct{}       { return c.done }
func (c fixedErrorContext) Err() error                  { return c.err }
func (fixedErrorContext) Value(any) any                 { return nil }

func (t cancelAtPollEOFTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	resp, err := t.base.RoundTrip(req)
	if err == nil && req.Method == http.MethodGet {
		resp.Body = &cancelAtEOFReadCloser{ReadCloser: resp.Body, cancel: t.cancel}
	}
	return resp, err
}

type cancelAtEOFReadCloser struct {
	io.ReadCloser
	cancel context.CancelFunc
	once   sync.Once
}

func (r *cancelAtEOFReadCloser) Read(p []byte) (int, error) {
	n, err := r.ReadCloser.Read(p)
	if err == io.EOF {
		r.once.Do(r.cancel)
	}
	return n, err
}

func newAppliance(t *testing.T, cfg mockappliance.Config) *mockappliance.Appliance {
	t.Helper()
	cfg.ContractPath = contractPath
	if cfg.SessionID == "" {
		cfg.SessionID = sessionToken
	}
	return mockappliance.New(t, cfg)
}

func newClient(t *testing.T, app *mockappliance.Appliance) *snapservice.Client {
	t.Helper()
	client, err := snapservice.NewClient(app.URL(), sessionToken)
	if err != nil {
		t.Fatalf("NewClient(%q): %v", app.URL(), err)
	}
	if client == nil {
		t.Fatal("NewClient returned a nil client and a nil error")
	}
	client.PollInterval = time.Millisecond
	client.MaxPolls = 32
	return client
}

func basicRequest() snapservice.SnapshotRequest {
	return snapservice.SnapshotRequest{
		Cluster:         clusterID,
		ProtectionGroup: pgID,
		Name:            "nightly-2026-05-13",
	}
}

func assertMatched(t *testing.T, r mockappliance.Request, wantOp string) {
	t.Helper()
	if !r.Matched {
		t.Fatalf("%s %s?%s matched no contract operation", r.Method, r.Path, r.RawQuery)
	}
	if r.OperationID != wantOp {
		t.Fatalf("%s %s matched operationId %q, want %q", r.Method, r.Path, r.OperationID, wantOp)
	}
}

func assertHeader(t *testing.T, r mockappliance.Request, name, want string) {
	t.Helper()
	if got := r.Header.Get(name); got != want {
		t.Errorf("%s %s header %s = %q, want %q", r.Method, r.Path, name, got, want)
	}
}

func assertNoHeader(t *testing.T, r mockappliance.Request, name string) {
	t.Helper()
	if got := r.Header.Get(name); got != "" {
		t.Errorf("%s %s sent header %s = %q, want none", r.Method, r.Path, name, got)
	}
}

func summarize(log []mockappliance.Request) string {
	if len(log) == 0 {
		return "(no requests)"
	}
	var b strings.Builder
	for i, r := range log {
		if i > 0 {
			b.WriteString(", ")
		}
		fmt.Fprintf(&b, "%s %s", r.Method, r.Path)
		if r.RawQuery != "" {
			fmt.Fprintf(&b, "?%s", r.RawQuery)
		}
	}
	return b.String()
}

func jsonEqual(got, want any) bool {
	a, err := json.Marshal(got)
	if err != nil {
		return false
	}
	b, err := json.Marshal(want)
	if err != nil {
		return false
	}
	var x, y any
	if json.Unmarshal(a, &x) != nil || json.Unmarshal(b, &y) != nil {
		return false
	}
	return reflect.DeepEqual(x, y)
}

func isHex40(s string) bool {
	if len(s) != 40 {
		return false
	}
	zeros := true
	for _, r := range s {
		switch {
		case r >= '0' && r <= '9':
			if r != '0' {
				zeros = false
			}
		case r >= 'a' && r <= 'f':
			zeros = false
		default:
			return false
		}
	}
	return !zeros
}
