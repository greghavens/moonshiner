// Acceptance harness for the taskwatch package: a loopback fake SDDC Manager
// speaking the cluster-update + Tasks contract pinned in docs/contract.json.
// No real appliance, no real credentials. Protected — do not modify.
package taskwatch_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"sync"
	"testing"
	"time"

	tw "go-vcf-task-watcher"
)

const token = "dummy-access-91c4e7d0" // dummy; never a real credential

const (
	clusterID = "c66f2b8e-04d1-4a3b-9c77-e5a8f1d20b94"
	taskID    = "7d5a1f36-9e0c-4b82-a6d4-08c3b7e51f29"
)

// ------------------------------------------------------------ fixtures

func taskJSON(status string) string {
	return fmt.Sprintf(`{
		"id": %q,
		"name": "Expanding cluster sfo-m01-cl01",
		"type": "CLUSTER_EXPANSION",
		"status": %q,
		"creationTimestamp": "2026-07-16T18:02:11.000Z",
		"resources": [
			{"resourceId": %q, "type": "CLUSTER", "name": "sfo-m01-cl01"}
		],
		"isCancellable": true,
		"isRetryable": false
	}`, taskID, status, clusterID)
}

const failedTaskJSON = `{
	"id": "7d5a1f36-9e0c-4b82-a6d4-08c3b7e51f29",
	"name": "Expanding cluster sfo-m01-cl01",
	"type": "CLUSTER_EXPANSION",
	"status": "FAILED",
	"creationTimestamp": "2026-07-16T18:02:11.000Z",
	"completionTimestamp": "2026-07-16T18:26:40.000Z",
	"resolutionStatus": "UNRESOLVED",
	"isCancellable": false,
	"isRetryable": true,
	"resources": [
		{"resourceId": "c66f2b8e-04d1-4a3b-9c77-e5a8f1d20b94", "type": "CLUSTER", "name": "sfo-m01-cl01"},
		{"resourceId": "0f9e4d21-6c3a-48b5-8a17-d2e90c4b7f68", "fqdn": "esx07.sfo.rainpole.io", "type": "ESXI", "name": "esx07"}
	],
	"errors": [
		{
			"errorCode": "VCF_CLUSTER_EXPANSION_FAILED",
			"errorType": "WORKFLOW",
			"message": "Cluster expansion failed for sfo-m01-cl01",
			"remediationMessage": "Inspect nested errors, remediate the host, then retry the task",
			"referenceToken": "AQ7D2K",
			"causes": [
				{"type": "EsxHostConnectionException", "message": "esx07.sfo.rainpole.io did not respond within 120s"}
			],
			"nestedErrors": [
				{
					"errorCode": "ESX_CONNECT_TIMEOUT",
					"message": "Timed out connecting to esx07.sfo.rainpole.io:443",
					"remediationMessage": "Verify the host is powered on and reachable from SDDC Manager",
					"referenceToken": "BX93MF",
					"nestedErrors": [
						{
							"errorCode": "NETWORK_UNREACHABLE",
							"message": "No route to host during TLS handshake"
						}
					]
				}
			]
		}
	],
	"subTasks": [
		{
			"name": "Add host esx07.sfo.rainpole.io to cluster",
			"description": "Join host to vCenter cluster and apply network profile",
			"status": "FAILED",
			"errors": [
				{
					"errorCode": "HOST_ADD_VMOTION_CONFIG",
					"message": "vMotion vmknic could not be configured",
					"remediationMessage": "Check the network profile uplinks for the host",
					"referenceToken": "AQ7D2K"
				}
			]
		},
		{
			"name": "Rebalance vSAN",
			"description": "Rebalance vSAN storage across cluster hosts",
			"status": "SKIPPED",
			"errors": []
		}
	]
}`

func expectedFailedTask() tw.Task {
	return tw.Task{
		ID:                  taskID,
		Name:                "Expanding cluster sfo-m01-cl01",
		Type:                "CLUSTER_EXPANSION",
		Status:              "FAILED",
		CreationTimestamp:   "2026-07-16T18:02:11.000Z",
		CompletionTimestamp: "2026-07-16T18:26:40.000Z",
		ResolutionStatus:    "UNRESOLVED",
		IsCancellable:       false,
		IsRetryable:         true,
		Resources: []tw.Resource{
			{ResourceID: clusterID, Type: "CLUSTER", Name: "sfo-m01-cl01"},
			{ResourceID: "0f9e4d21-6c3a-48b5-8a17-d2e90c4b7f68", FQDN: "esx07.sfo.rainpole.io", Type: "ESXI", Name: "esx07"},
		},
		Errors: []tw.TaskError{
			{
				ErrorCode:          "VCF_CLUSTER_EXPANSION_FAILED",
				ErrorType:          "WORKFLOW",
				Message:            "Cluster expansion failed for sfo-m01-cl01",
				RemediationMessage: "Inspect nested errors, remediate the host, then retry the task",
				ReferenceToken:     "AQ7D2K",
				Causes: []tw.Cause{
					{Type: "EsxHostConnectionException", Message: "esx07.sfo.rainpole.io did not respond within 120s"},
				},
				NestedErrors: []tw.TaskError{
					{
						ErrorCode:          "ESX_CONNECT_TIMEOUT",
						Message:            "Timed out connecting to esx07.sfo.rainpole.io:443",
						RemediationMessage: "Verify the host is powered on and reachable from SDDC Manager",
						ReferenceToken:     "BX93MF",
						NestedErrors: []tw.TaskError{
							{
								ErrorCode: "NETWORK_UNREACHABLE",
								Message:   "No route to host during TLS handshake",
							},
						},
					},
				},
			},
		},
		SubTasks: []tw.SubTask{
			{
				Name:        "Add host esx07.sfo.rainpole.io to cluster",
				Description: "Join host to vCenter cluster and apply network profile",
				Status:      "FAILED",
				Errors: []tw.TaskError{
					{
						ErrorCode:          "HOST_ADD_VMOTION_CONFIG",
						Message:            "vMotion vmknic could not be configured",
						RemediationMessage: "Check the network profile uplinks for the host",
						ReferenceToken:     "AQ7D2K",
					},
				},
			},
			{
				Name:        "Rebalance vSAN",
				Description: "Rebalance vSAN storage across cluster hosts",
				Status:      "SKIPPED",
				Errors:      []tw.TaskError{},
			},
		},
	}
}

const errorEnvelope500 = `{
	"errorCode": "VCF_SYSTEM_ERROR",
	"message": "Internal server error while reading task",
	"remediationMessage": "Retry later",
	"referenceToken": "R5T0PQ"
}`

const errorEnvelope404 = `{
	"errorCode": "TASK_NOT_FOUND",
	"message": "Task with id was not found",
	"referenceToken": "M4J8WD"
}`

// ------------------------------------------------------------ fake server

type scripted struct {
	status   int
	body     string
	location string
}

type fakeSDDC struct {
	mu       sync.Mutex
	scripts  map[string][]scripted // "METHOD path" -> queue (last repeats)
	requests []recordedReq
}

type recordedReq struct {
	method string
	path   string
	auth   string
	accept string
	ctype  string
	body   []byte
}

func newFake() *fakeSDDC {
	return &fakeSDDC{scripts: map[string][]scripted{}}
}

func (f *fakeSDDC) script(method, path string, responses ...scripted) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.scripts[method+" "+path] = responses
}

func (f *fakeSDDC) seen(method, path string) []recordedReq {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []recordedReq
	for _, r := range f.requests {
		if r.method == method && r.path == path {
			out = append(out, r)
		}
	}
	return out
}

func (f *fakeSDDC) clear() {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.requests = nil
}

func (f *fakeSDDC) handler(t *testing.T) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		f.mu.Lock()
		f.requests = append(f.requests, recordedReq{
			method: r.Method,
			path:   r.URL.Path,
			auth:   r.Header.Get("Authorization"),
			accept: r.Header.Get("Accept"),
			ctype:  r.Header.Get("Content-Type"),
			body:   body,
		})
		key := r.Method + " " + r.URL.Path
		queue := f.scripts[key]
		var resp scripted
		switch {
		case len(queue) == 0:
			resp = scripted{status: 404, body: errorEnvelope404}
		case len(queue) == 1:
			resp = queue[0]
		default:
			resp = queue[0]
			f.scripts[key] = queue[1:]
		}
		f.mu.Unlock()

		if r.Header.Get("Authorization") != "Bearer "+token {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(401)
			fmt.Fprint(w, `{"errorCode":"UNAUTHORIZED","message":"Authentication required"}`)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if resp.location != "" {
			w.Header().Set("Location", resp.location)
		}
		w.WriteHeader(resp.status)
		fmt.Fprint(w, resp.body)
	})
}

func start(t *testing.T) (*fakeSDDC, *tw.Client, *httptest.Server) {
	t.Helper()
	fake := newFake()
	srv := httptest.NewServer(fake.handler(t))
	t.Cleanup(srv.Close)
	client := tw.NewClient(srv.URL, token, srv.Client())
	return fake, client, srv
}

func recordingSleep(sleeps *[]time.Duration, mu *sync.Mutex) func(context.Context, time.Duration) error {
	return func(ctx context.Context, d time.Duration) error {
		mu.Lock()
		*sleeps = append(*sleeps, d)
		mu.Unlock()
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
			return nil
		}
	}
}

// ------------------------------------------------------------ tests

func TestExpandClusterSubmitsDocumentedPatch(t *testing.T) {
	fake, client, srv := start(t)
	fake.script("PATCH", "/v1/clusters/"+clusterID,
		scripted{status: 202, body: taskJSON("PENDING"), location: srv.URL + "/v1/tasks/" + taskID})

	spec := tw.ClusterExpansionSpec{HostSpecs: []tw.HostSpec{
		{ID: "0f9e4d21-6c3a-48b5-8a17-d2e90c4b7f68"},
		{ID: "77ab34c9-2e05-4f61-b3d8-91c04ae56f22", AzName: "az1"},
	}}
	task, err := client.ExpandCluster(context.Background(), clusterID, spec)
	if err != nil {
		t.Fatalf("ExpandCluster: %v", err)
	}
	if task.ID != taskID || task.Status != "PENDING" {
		t.Fatalf("unexpected accepted task: %+v", task)
	}

	reqs := fake.seen("PATCH", "/v1/clusters/"+clusterID)
	if len(reqs) != 1 {
		t.Fatalf("expected exactly 1 PATCH, got %d", len(reqs))
	}
	r := reqs[0]
	if r.auth != "Bearer "+token {
		t.Fatalf("wrong Authorization header: %q", r.auth)
	}
	if r.ctype != "application/json" {
		t.Fatalf("wrong Content-Type: %q", r.ctype)
	}
	var got map[string]any
	if err := json.Unmarshal(r.body, &got); err != nil {
		t.Fatalf("PATCH body is not JSON: %v", err)
	}
	want := map[string]any{
		"clusterExpansionSpec": map[string]any{
			"hostSpecs": []any{
				map[string]any{"id": "0f9e4d21-6c3a-48b5-8a17-d2e90c4b7f68"},
				map[string]any{"id": "77ab34c9-2e05-4f61-b3d8-91c04ae56f22", "azName": "az1"},
			},
		},
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("PATCH body mismatch\n got: %#v\nwant: %#v", got, want)
	}
}

func TestExpandClusterValidatesLocationHeader(t *testing.T) {
	fake, client, srv := start(t)

	fake.script("PATCH", "/v1/clusters/"+clusterID,
		scripted{status: 202, body: taskJSON("PENDING"), location: srv.URL + "/v1/tasks/some-other-task"})
	_, err := client.ExpandCluster(context.Background(), clusterID, tw.ClusterExpansionSpec{})
	if err == nil {
		t.Fatal("mismatched Location must be an error")
	}
	if !contains(err.Error(), taskID) || !contains(err.Error(), "some-other-task") {
		t.Fatalf("location mismatch error must name both ids, got: %v", err)
	}

	fake.script("PATCH", "/v1/clusters/"+clusterID,
		scripted{status: 202, body: taskJSON("PENDING")})
	_, err = client.ExpandCluster(context.Background(), clusterID, tw.ClusterExpansionSpec{})
	if err == nil {
		t.Fatal("missing Location header must be an error")
	}
}

func TestExpandClusterDecodesErrorEnvelope(t *testing.T) {
	fake, client, _ := start(t)
	fake.script("PATCH", "/v1/clusters/"+clusterID,
		scripted{status: 403, body: `{"errorCode":"FORBIDDEN","message":"Insufficient role","referenceToken":"F0RB1D"}`})
	_, err := client.ExpandCluster(context.Background(), clusterID, tw.ClusterExpansionSpec{})
	var apiErr *tw.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("want *APIError, got %T: %v", err, err)
	}
	if apiErr.StatusCode != 403 || apiErr.ErrorCode != "FORBIDDEN" || apiErr.ReferenceToken != "F0RB1D" {
		t.Fatalf("error envelope not preserved: %+v", apiErr)
	}
}

func TestWaitForTaskBoundedBackoff(t *testing.T) {
	fake, client, _ := start(t)
	fake.script("GET", "/v1/tasks/"+taskID,
		scripted{status: 200, body: taskJSON("PENDING")},
		scripted{status: 200, body: taskJSON("QUEUED")},
		scripted{status: 200, body: taskJSON("IN_PROGRESS")},
		scripted{status: 200, body: taskJSON("IN_PROGRESS")},
		scripted{status: 200, body: taskJSON("IN_PROGRESS")},
		scripted{status: 200, body: taskJSON("IN_PROGRESS")},
		scripted{status: 200, body: taskJSON("SUCCESSFUL")},
	)

	var mu sync.Mutex
	var sleeps []time.Duration
	task, err := tw.WaitForTask(context.Background(), client, taskID, tw.WaitOptions{
		InitialDelay: 100 * time.Millisecond,
		MaxDelay:     800 * time.Millisecond,
		Sleep:        recordingSleep(&sleeps, &mu),
	})
	if err != nil {
		t.Fatalf("WaitForTask: %v", err)
	}
	if task.Status != "SUCCESSFUL" {
		t.Fatalf("want terminal SUCCESSFUL, got %q", task.Status)
	}
	if n := len(fake.seen("GET", "/v1/tasks/"+taskID)); n != 7 {
		t.Fatalf("expected 7 polls, got %d", n)
	}
	want := []time.Duration{
		100 * time.Millisecond, 200 * time.Millisecond, 400 * time.Millisecond,
		800 * time.Millisecond, 800 * time.Millisecond, 800 * time.Millisecond,
	}
	mu.Lock()
	defer mu.Unlock()
	if !reflect.DeepEqual(sleeps, want) {
		t.Fatalf("backoff schedule mismatch\n got: %v\nwant: %v", sleeps, want)
	}
}

func TestWaitForTaskFailedIsAResultWithErrorsIntact(t *testing.T) {
	fake, client, _ := start(t)
	fake.script("GET", "/v1/tasks/"+taskID,
		scripted{status: 200, body: taskJSON("IN_PROGRESS")},
		scripted{status: 200, body: failedTaskJSON},
	)

	var mu sync.Mutex
	var sleeps []time.Duration
	task, err := tw.WaitForTask(context.Background(), client, taskID, tw.WaitOptions{
		InitialDelay: 50 * time.Millisecond,
		MaxDelay:     400 * time.Millisecond,
		Sleep:        recordingSleep(&sleeps, &mu),
	})
	var failed *tw.TaskFailedError
	if !errors.As(err, &failed) {
		t.Fatalf("want *TaskFailedError, got %T: %v", err, err)
	}
	if !reflect.DeepEqual(task, expectedFailedTask()) {
		t.Fatalf("terminal task not preserved verbatim\n got: %+v\nwant: %+v", task, expectedFailedTask())
	}
	if !reflect.DeepEqual(failed.Task, expectedFailedTask()) {
		t.Fatalf("TaskFailedError must wrap the terminal task")
	}
	tokens := tw.ReferenceTokens(task)
	wantTokens := []string{"AQ7D2K", "BX93MF"}
	if !reflect.DeepEqual(tokens, wantTokens) {
		t.Fatalf("ReferenceTokens mismatch (depth-first, deduped, first wins)\n got: %v\nwant: %v", tokens, wantTokens)
	}
}

func TestWaitForTaskTransientServerErrors(t *testing.T) {
	fake, client, _ := start(t)
	fake.script("GET", "/v1/tasks/"+taskID,
		scripted{status: 200, body: taskJSON("IN_PROGRESS")},
		scripted{status: 500, body: errorEnvelope500},
		scripted{status: 503, body: errorEnvelope500},
		scripted{status: 200, body: taskJSON("IN_PROGRESS")},
		scripted{status: 200, body: taskJSON("SUCCESSFUL")},
	)
	var mu sync.Mutex
	var sleeps []time.Duration
	task, err := tw.WaitForTask(context.Background(), client, taskID, tw.WaitOptions{
		InitialDelay: 100 * time.Millisecond,
		MaxDelay:     800 * time.Millisecond,
		Sleep:        recordingSleep(&sleeps, &mu),
	})
	if err != nil {
		t.Fatalf("two transient 5xx must be survived: %v", err)
	}
	if task.Status != "SUCCESSFUL" {
		t.Fatalf("want SUCCESSFUL after transient recovery, got %q", task.Status)
	}
	if n := len(fake.seen("GET", "/v1/tasks/"+taskID)); n != 5 {
		t.Fatalf("expected 5 reads, got %d", n)
	}

	fake.clear()
	fake.script("GET", "/v1/tasks/"+taskID, scripted{status: 500, body: errorEnvelope500})
	_, err = tw.WaitForTask(context.Background(), client, taskID, tw.WaitOptions{
		InitialDelay: 100 * time.Millisecond,
		MaxDelay:     800 * time.Millisecond,
		Sleep:        recordingSleep(&sleeps, &mu),
	})
	var apiErr *tw.APIError
	if !errors.As(err, &apiErr) || apiErr.StatusCode != 500 {
		t.Fatalf("three consecutive 5xx must surface *APIError(500), got %T: %v", err, err)
	}
	if apiErr.ErrorCode != "VCF_SYSTEM_ERROR" || apiErr.ReferenceToken != "R5T0PQ" {
		t.Fatalf("error envelope not preserved on give-up: %+v", apiErr)
	}
	if n := len(fake.seen("GET", "/v1/tasks/"+taskID)); n != 3 {
		t.Fatalf("give-up after exactly 3 consecutive 5xx reads, got %d", n)
	}
}

func TestWaitForTaskNotFoundIsImmediate(t *testing.T) {
	fake, client, _ := start(t)
	fake.script("GET", "/v1/tasks/"+taskID, scripted{status: 404, body: errorEnvelope404})
	var mu sync.Mutex
	var sleeps []time.Duration
	_, err := tw.WaitForTask(context.Background(), client, taskID, tw.WaitOptions{
		InitialDelay: 100 * time.Millisecond,
		MaxDelay:     800 * time.Millisecond,
		Sleep:        recordingSleep(&sleeps, &mu),
	})
	var apiErr *tw.APIError
	if !errors.As(err, &apiErr) || apiErr.StatusCode != 404 {
		t.Fatalf("404 must surface immediately as *APIError, got %T: %v", err, err)
	}
	if apiErr.ErrorCode != "TASK_NOT_FOUND" || apiErr.ReferenceToken != "M4J8WD" {
		t.Fatalf("404 envelope not preserved: %+v", apiErr)
	}
	if n := len(fake.seen("GET", "/v1/tasks/"+taskID)); n != 1 {
		t.Fatalf("404 must not be retried, got %d reads", n)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(sleeps) != 0 {
		t.Fatalf("404 must not sleep, got %v", sleeps)
	}
}

func TestWaitForTaskContextCancellation(t *testing.T) {
	fake, client, _ := start(t)
	fake.script("GET", "/v1/tasks/"+taskID, scripted{status: 200, body: taskJSON("IN_PROGRESS")})

	ctx, cancel := context.WithCancel(context.Background())
	var mu sync.Mutex
	var count int
	sleep := func(c context.Context, d time.Duration) error {
		mu.Lock()
		count++
		n := count
		mu.Unlock()
		if n == 2 {
			cancel()
			return c.Err()
		}
		return nil
	}
	_, err := tw.WaitForTask(ctx, client, taskID, tw.WaitOptions{
		InitialDelay: 100 * time.Millisecond,
		MaxDelay:     800 * time.Millisecond,
		Sleep:        sleep,
	})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled wait must return ctx.Err(), got %v", err)
	}
	if n := len(fake.seen("GET", "/v1/tasks/"+taskID)); n != 2 {
		t.Fatalf("polling must stop promptly on cancellation, got %d reads", n)
	}
}

func TestWaitForTaskRejectsUnknownStatus(t *testing.T) {
	fake, client, _ := start(t)
	fake.script("GET", "/v1/tasks/"+taskID, scripted{status: 200, body: taskJSON("STARTED")})
	var mu sync.Mutex
	var sleeps []time.Duration
	_, err := tw.WaitForTask(context.Background(), client, taskID, tw.WaitOptions{
		InitialDelay: 100 * time.Millisecond,
		MaxDelay:     800 * time.Millisecond,
		Sleep:        recordingSleep(&sleeps, &mu),
	})
	if err == nil || !contains(err.Error(), "STARTED") {
		t.Fatalf("undocumented status must be an error naming the status, got: %v", err)
	}
}

func TestCancelTask(t *testing.T) {
	fake, client, _ := start(t)
	fake.script("DELETE", "/v1/tasks/"+taskID, scripted{status: 200, body: taskJSON("CANCELLED")})
	task, err := client.CancelTask(context.Background(), taskID)
	if err != nil {
		t.Fatalf("CancelTask: %v", err)
	}
	if task.Status != "CANCELLED" {
		t.Fatalf("want CANCELLED task back, got %q", task.Status)
	}
	reqs := fake.seen("DELETE", "/v1/tasks/"+taskID)
	if len(reqs) != 1 {
		t.Fatalf("expected exactly one DELETE, got %d", len(reqs))
	}
	if reqs[0].auth != "Bearer "+token {
		t.Fatalf("wrong Authorization on DELETE: %q", reqs[0].auth)
	}
	if !tw.IsTerminal("CANCELLED") {
		t.Fatal("CANCELLED must be terminal")
	}
}

func TestTerminalStatusTable(t *testing.T) {
	for _, s := range []string{"SUCCESSFUL", "FAILED", "CANCELLED", "COMPLETED_WITH_WARNING", "SKIPPED", "TIMED_OUT"} {
		if !tw.IsTerminal(s) {
			t.Errorf("%s must be terminal", s)
		}
	}
	for _, s := range []string{"PENDING", "QUEUED", "IN_PROGRESS"} {
		if tw.IsTerminal(s) {
			t.Errorf("%s must not be terminal", s)
		}
	}
}

func TestDocsFixturesParse(t *testing.T) {
	for _, name := range []string{"docs/contract.json", "docs/official_sources.json"} {
		raw, err := os.ReadFile(name)
		if err != nil {
			t.Fatalf("read %s: %v", name, err)
		}
		var v any
		if err := json.Unmarshal(raw, &v); err != nil {
			t.Fatalf("%s is not valid JSON: %v", name, err)
		}
	}
}

func contains(haystack, needle string) bool {
	return len(needle) > 0 && len(haystack) >= len(needle) && indexOf(haystack, needle) >= 0
}

func indexOf(haystack, needle string) int {
	for i := 0; i+len(needle) <= len(haystack); i++ {
		if haystack[i:i+len(needle)] == needle {
			return i
		}
	}
	return -1
}
