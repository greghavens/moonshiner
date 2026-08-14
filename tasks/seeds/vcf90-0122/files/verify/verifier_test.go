package verify_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	vsandp "example.com/vcf90/vsandp"
)

const (
	createOperationID = "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task"
	taskOperationID   = "Snapservice.Tasks_get"
	commitSHA         = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
)

type contractDocument struct {
	BasePath     string              `json:"base_path"`
	Operations   []contractOperation `json:"operations"`
	TaskStatuses taskStatuses        `json:"task_statuses"`
}

type contractOperation struct {
	OperationID   string            `json:"operation_id"`
	Method        string            `json:"method"`
	Path          string            `json:"path"`
	RequiredQuery map[string]string `json:"required_query,omitempty"`
	Request       *contractRequest  `json:"request,omitempty"`
	Success       contractSuccess   `json:"success"`
}

type contractRequest struct {
	ContentType       string   `json:"content_type"`
	Schema            string   `json:"schema"`
	Required          []string `json:"required"`
	Optional          []string `json:"optional"`
	RetentionRequired []string `json:"retention_required"`
}

type contractSuccess struct {
	Status      int      `json:"status"`
	ContentType string   `json:"content_type"`
	Schema      string   `json:"schema"`
	Semantic    string   `json:"semantic,omitempty"`
	Required    []string `json:"required,omitempty"`
}

type taskStatuses struct {
	NonTerminal []string `json:"non_terminal"`
	Terminal    []string `json:"terminal"`
}

type officialSources struct {
	Repository   string   `json:"repository"`
	License      string   `json:"license"`
	SpecPath     string   `json:"spec_path"`
	Tag          string   `json:"tag"`
	CommitSHA    string   `json:"commit_sha"`
	OperationIDs []string `json:"operation_ids"`
}

func projectFile(t *testing.T, elems ...string) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate verifier source")
	}
	parts := append([]string{filepath.Dir(file), ".."}, elems...)
	return filepath.Clean(filepath.Join(parts...))
}

func decodeFile[T any](t *testing.T, name string) T {
	t.Helper()
	data, err := os.ReadFile(projectFile(t, "docs", name))
	if err != nil {
		t.Fatalf("read %s: %v", name, err)
	}
	var got T
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&got); err != nil {
		t.Fatalf("decode %s: %v", name, err)
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		t.Fatalf("decode %s: trailing JSON content", name)
	}
	return got
}

func TestOfficialSpecArtifacts(t *testing.T) {
	wantContract := contractDocument{
		BasePath: "/api",
		Operations: []contractOperation{
			{
				OperationID:   createOperationID,
				Method:        http.MethodPost,
				Path:          "/snapservice/clusters/{cluster}/protection-groups/{pg}/snapshots",
				RequiredQuery: map[string]string{"vmw-task": "true"},
				Request: &contractRequest{
					ContentType:       "application/json",
					Schema:            "Snapservice.Clusters.ProtectionGroups.Snapshots.CreateSpec",
					Required:          []string{"name"},
					Optional:          []string{"retention"},
					RetentionRequired: []string{"duration", "unit"},
				},
				Success: contractSuccess{
					Status:      http.StatusAccepted,
					ContentType: "application/json",
					Schema:      "string",
					Semantic:    "com.vmware.snapservice.task identifier",
				},
			},
			{
				OperationID: taskOperationID,
				Method:      http.MethodGet,
				Path:        "/snapservice/tasks/{task}",
				Success: contractSuccess{
					Status:      http.StatusOK,
					ContentType: "application/json",
					Schema:      "Snapservice.Tasks.Info",
					Required:    []string{"cancelable", "description", "operation", "service", "status"},
				},
			},
		},
		TaskStatuses: taskStatuses{
			NonTerminal: []string{"PENDING", "RUNNING", "BLOCKED"},
			Terminal:    []string{"SUCCEEDED", "FAILED"},
		},
	}
	if got := decodeFile[contractDocument](t, "contract.json"); !reflect.DeepEqual(got, wantContract) {
		t.Fatalf("contract.json does not match the 9.0 specification\ngot:  %#v\nwant: %#v", got, wantContract)
	}

	wantSources := officialSources{
		Repository: "https://github.com/vmware/vcf-api-specs",
		License:    "Apache-2.0",
		SpecPath:   "specifications/vsan-data-protection/vsan-data-protection-openapi.yaml",
		Tag:        "9.0.0.0",
		CommitSHA:  commitSHA,
		OperationIDs: []string{
			createOperationID,
			taskOperationID,
		},
	}
	if got := decodeFile[officialSources](t, "official_sources.json"); !reflect.DeepEqual(got, wantSources) {
		t.Fatalf("official_sources.json is not pinned to the requested source\ngot:  %#v\nwant: %#v", got, wantSources)
	}
}

func TestMockRejectsContractDrift(t *testing.T) {
	tests := []struct {
		name   string
		mutate func(map[string]any)
	}{
		{
			name: "operation request schema",
			mutate: func(contract map[string]any) {
				operations := contract["operations"].([]any)
				create := operations[0].(map[string]any)
				request := create["request"].(map[string]any)
				request["schema"] = "Drifted.CreateSpec"
			},
		},
		{
			name: "task statuses",
			mutate: func(contract map[string]any) {
				statuses := contract["task_statuses"].(map[string]any)
				statuses["terminal"] = []any{"SUCCEEDED", "FAILED", "CANCELED"}
			},
		},
		{
			name: "unknown contract field",
			mutate: func(contract map[string]any) {
				contract["unrecognized"] = true
			},
		},
	}

	for i := range tests {
		testCase := tests[i]
		t.Run(testCase.name, func(t *testing.T) {
			moduleDir := prepareDriftModule(t, testCase.mutate)
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, "go", "test", "-run", "^TestRejectsContractDrift$", "./")
			cmd.Dir = moduleDir
			cmd.Env = append(os.Environ(), "GOPROXY=off")
			output, err := cmd.CombinedOutput()
			if ctx.Err() != nil {
				t.Fatalf("drift verification timed out: %v", ctx.Err())
			}
			if err != nil {
				t.Fatalf("mock accepted %s drift: %v\n%s", testCase.name, err, output)
			}
		})
	}
}

func prepareDriftModule(t *testing.T, mutate func(map[string]any)) string {
	t.Helper()
	moduleDir := t.TempDir()
	root := projectFile(t)
	entries, err := os.ReadDir(root)
	if err != nil {
		t.Fatalf("read module root: %v", err)
	}
	for _, entry := range entries {
		name := entry.Name()
		if entry.IsDir() || (name != "go.mod" && (!strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go"))) {
			continue
		}
		data, err := os.ReadFile(filepath.Join(root, name))
		if err != nil {
			t.Fatalf("read %s: %v", name, err)
		}
		if err := os.WriteFile(filepath.Join(moduleDir, name), data, 0o600); err != nil {
			t.Fatalf("copy %s: %v", name, err)
		}
	}

	data, err := os.ReadFile(filepath.Join(root, "docs", "contract.json"))
	if err != nil {
		t.Fatalf("read contract for drift check: %v", err)
	}
	var contract map[string]any
	if err := json.Unmarshal(data, &contract); err != nil {
		t.Fatalf("decode contract for drift check: %v", err)
	}
	mutate(contract)
	data, err = json.Marshal(contract)
	if err != nil {
		t.Fatalf("encode drifted contract: %v", err)
	}
	if err := os.Mkdir(filepath.Join(moduleDir, "docs"), 0o700); err != nil {
		t.Fatalf("create drifted docs directory: %v", err)
	}
	if err := os.WriteFile(filepath.Join(moduleDir, "docs", "contract.json"), data, 0o600); err != nil {
		t.Fatalf("write drifted contract: %v", err)
	}

	const driftTest = `package vsandp_test

import (
	"testing"

	vsandp "example.com/vcf90/vsandp"
)

func TestRejectsContractDrift(t *testing.T) {
	mock, err := vsandp.NewMockServer(vsandp.MockScenario{
		Statuses: []vsandp.TaskStatus{vsandp.TaskSucceeded},
	})
	if err == nil {
		mock.Close()
		t.Fatal("NewMockServer accepted a drifted contract")
	}
}
`
	if err := os.WriteFile(filepath.Join(moduleDir, "drift_test.go"), []byte(driftTest), 0o600); err != nil {
		t.Fatalf("write drift verifier: %v", err)
	}
	return moduleDir
}

func TestCreateSnapshotAndWaitEscapesPathSegments(t *testing.T) {
	t.Parallel()

	scenario := vsandp.MockScenario{
		ClusterID:         "domain/c 8",
		ProtectionGroupID: "pg/blue ?",
		TaskID:            "task/42 ?",
		Statuses:          []vsandp.TaskStatus{vsandp.TaskSucceeded},
	}
	mock, err := vsandp.NewMockServer(scenario)
	if err != nil {
		t.Fatalf("NewMockServer: %v", err)
	}
	defer mock.Close()

	client := vsandp.NewClient(mock.URL(), "session-abc", mock.Client(), 0)
	if _, err := client.CreateSnapshotAndWait(context.Background(), scenario.ClusterID, scenario.ProtectionGroupID, vsandp.SnapshotCreateSpec{Name: "escaped"}); err != nil {
		t.Fatalf("CreateSnapshotAndWait: %v", err)
	}

	requests := mock.Requests()
	if got, want := len(requests), 2; got != want {
		t.Fatalf("request count = %d, want %d", got, want)
	}
	if got, want := requests[0].RequestURI, "/api/snapservice/clusters/domain%2Fc%208/protection-groups/pg%2Fblue%20%3F/snapshots?vmw-task=true"; got != want {
		t.Errorf("create request URI = %q, want %q", got, want)
	}
	if got, want := requests[1].RequestURI, "/api/snapservice/tasks/task%2F42%20%3F"; got != want {
		t.Errorf("task request URI = %q, want %q", got, want)
	}
}

func TestCreateSnapshotAndWaitWireContract(t *testing.T) {
	t.Parallel()

	tests := []struct {
		name       string
		spec       vsandp.SnapshotCreateSpec
		statuses   []vsandp.TaskStatus
		wantBody   string
		wantFailed bool
	}{
		{
			name:     "unset optional retention is omitted and every nonterminal state is polled",
			spec:     vsandp.SnapshotCreateSpec{Name: "manual-before-upgrade"},
			statuses: []vsandp.TaskStatus{vsandp.TaskPending, vsandp.TaskRunning, vsandp.TaskBlocked, vsandp.TaskSucceeded},
			wantBody: `{"name":"manual-before-upgrade"}`,
		},
		{
			name: "configured retention uses specification wire names",
			spec: vsandp.SnapshotCreateSpec{
				Name:      "short-lived",
				Retention: &vsandp.RetentionPeriod{Unit: "HOUR", Duration: 6},
			},
			statuses: []vsandp.TaskStatus{vsandp.TaskSucceeded},
			wantBody: `{"name":"short-lived","retention":{"unit":"HOUR","duration":6}}`,
		},
		{
			name:       "failed is terminal",
			spec:       vsandp.SnapshotCreateSpec{Name: "will-fail"},
			statuses:   []vsandp.TaskStatus{vsandp.TaskRunning, vsandp.TaskFailed},
			wantBody:   `{"name":"will-fail"}`,
			wantFailed: true,
		},
	}

	for _, tt := range tests {
		tt := tt
		t.Run(tt.name, func(t *testing.T) {
			t.Parallel()
			scenario := vsandp.MockScenario{
				ClusterID:         "domain-c8",
				ProtectionGroupID: "pg-blue",
				TaskID:            "task-42",
				Statuses:          tt.statuses,
			}
			mock, err := vsandp.NewMockServer(scenario)
			if err != nil {
				t.Fatalf("NewMockServer: %v", err)
			}
			defer mock.Close()

			client := vsandp.NewClient(mock.URL(), "session-abc", mock.Client(), time.Millisecond)
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			got, err := client.CreateSnapshotAndWait(ctx, scenario.ClusterID, scenario.ProtectionGroupID, tt.spec)
			if tt.wantFailed {
				var failed *vsandp.TaskFailedError
				if !errors.As(err, &failed) {
					t.Fatalf("error = %v, want TaskFailedError", err)
				}
				if failed.Task.Status != vsandp.TaskFailed {
					t.Fatalf("failed task status = %q", failed.Task.Status)
				}
			} else {
				if err != nil {
					t.Fatalf("CreateSnapshotAndWait: %v", err)
				}
				if got.Status != vsandp.TaskSucceeded {
					t.Fatalf("final status = %q, want SUCCEEDED", got.Status)
				}
			}

			requests := mock.Requests()
			if got, want := len(requests), 1+len(tt.statuses); got != want {
				t.Fatalf("request count = %d, want %d", got, want)
			}
			assertCreateRequest(t, requests[0], tt.wantBody)
			for i, request := range requests[1:] {
				assertTaskRequest(t, request, i)
			}
		})
	}
}

func assertCreateRequest(t *testing.T, got vsandp.RequestRecord, wantBody string) {
	t.Helper()
	if got.Method != http.MethodPost {
		t.Errorf("create method = %q", got.Method)
	}
	if got.RequestURI != "/api/snapservice/clusters/domain-c8/protection-groups/pg-blue/snapshots?vmw-task=true" {
		t.Errorf("create request URI = %q", got.RequestURI)
	}
	if string(got.Body) != wantBody {
		t.Errorf("create body = %q, want %q", got.Body, wantBody)
	}
	if got.ContentLength != int64(len(wantBody)) {
		t.Errorf("create content length = %d, want %d", got.ContentLength, len(wantBody))
	}
	if got.Header.Get("Content-Type") != "application/json" {
		t.Errorf("create Content-Type = %q", got.Header.Get("Content-Type"))
	}
	assertCommonHeaders(t, got)
}

func assertTaskRequest(t *testing.T, got vsandp.RequestRecord, index int) {
	t.Helper()
	if got.Method != http.MethodGet {
		t.Errorf("task request %d method = %q", index, got.Method)
	}
	if got.RequestURI != "/api/snapservice/tasks/task-42" {
		t.Errorf("task request %d URI = %q", index, got.RequestURI)
	}
	if len(got.Body) != 0 || got.ContentLength != 0 {
		t.Errorf("task request %d unexpectedly has body %q (length %d)", index, got.Body, got.ContentLength)
	}
	if got.Header.Get("Content-Type") != "" {
		t.Errorf("task request %d unexpectedly has Content-Type %q", index, got.Header.Get("Content-Type"))
	}
	assertCommonHeaders(t, got)
}

func assertCommonHeaders(t *testing.T, got vsandp.RequestRecord) {
	t.Helper()
	if got.Header.Get("Accept") != "application/json" {
		t.Errorf("Accept = %q", got.Header.Get("Accept"))
	}
	if got.Header.Get("vmware-api-session-id") != "session-abc" {
		t.Errorf("vmware-api-session-id = %q", got.Header.Get("vmware-api-session-id"))
	}
}

func TestMockServesOnlyContractOperationsAndCopiesLog(t *testing.T) {
	mock, err := vsandp.NewMockServer(vsandp.MockScenario{
		ClusterID:         "domain-c8",
		ProtectionGroupID: "pg-blue",
		TaskID:            "task-42",
		Statuses:          []vsandp.TaskStatus{vsandp.TaskSucceeded},
	})
	if err != nil {
		t.Fatalf("NewMockServer: %v", err)
	}
	defer mock.Close()

	tests := []struct {
		method string
		path   string
	}{
		{method: http.MethodGet, path: "/api/snapservice/sessions"},
		{method: http.MethodPost, path: "/api/snapservice/tasks/task-42"},
		{method: http.MethodGet, path: "/api/snapservice/tasks/task-42?extra=true"},
		{method: http.MethodPost, path: "/api/snapservice/clusters/domain-c8/protection-groups/pg-blue/snapshots"},
		{method: http.MethodPost, path: "/api/snapservice/clusters/domain-c8/protection-groups/pg-blue/snapshots?vmw-task=false"},
		{method: http.MethodPost, path: "/api/snapservice/clusters/domain-c8/protection-groups/pg-blue/snapshots?vmw-task=true&extra=true"},
	}
	for _, tt := range tests {
		req, err := http.NewRequest(tt.method, mock.URL()+tt.path[len("/api"):], nil)
		if err != nil {
			t.Fatalf("build %s %s: %v", tt.method, tt.path, err)
		}
		resp, err := mock.Client().Do(req)
		if err != nil {
			t.Fatalf("%s %s: %v", tt.method, tt.path, err)
		}
		_, _ = io.Copy(io.Discard, resp.Body)
		_ = resp.Body.Close()
		if resp.StatusCode != http.StatusNotFound {
			t.Errorf("%s %s status = %d, want 404", tt.method, tt.path, resp.StatusCode)
		}
	}

	first := mock.Requests()
	if len(first) != len(tests) {
		t.Fatalf("logged requests = %d, want %d", len(first), len(tests))
	}
	first[0].Header.Set("mutated", "yes")
	first[0].Body = append(first[0].Body, 'x')
	second := mock.Requests()
	if second[0].Header.Get("mutated") != "" || !reflect.DeepEqual(second[0].Body, []byte{}) {
		t.Fatal("Requests did not return a deep copy")
	}

	const concurrentRequests = 32
	var wg sync.WaitGroup
	wg.Add(concurrentRequests)
	for i := 0; i < concurrentRequests; i++ {
		go func(i int) {
			defer wg.Done()
			resp, err := mock.Client().Get(fmt.Sprintf("%s/unknown/%d", mock.URL(), i))
			if err != nil {
				t.Errorf("concurrent request %d: %v", i, err)
				return
			}
			_, _ = io.Copy(io.Discard, resp.Body)
			_ = resp.Body.Close()
			if resp.StatusCode != http.StatusNotFound {
				t.Errorf("concurrent request %d status = %d, want 404", i, resp.StatusCode)
			}
		}(i)
	}
	for i := 0; i < concurrentRequests; i++ {
		_ = mock.Requests()
	}
	wg.Wait()
	if got, want := len(mock.Requests()), len(tests)+concurrentRequests; got != want {
		t.Fatalf("logged requests after concurrent access = %d, want %d", got, want)
	}
}
