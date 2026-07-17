// Acceptance harness for the mbatch package: a loopback fake of the Cisco
// Meraki Dashboard API v1 action-batch resource, pinned in
// docs/contract.json (provenance: docs/official_sources.json).
// Hermetic: no real dashboard, no real key, no time.Sleep in the package.
// Protected -- do not modify. Run: go test -race -timeout 30s ./...
package mbatch_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	mbatch "go-meraki-action-batch"
)

const (
	apiKey = "beefbeefbeefbeefbeefbeefbeefbeefbeefbeef-fake"
	orgID  = "612"
)

func check(t *testing.T, cond bool, format string, args ...any) {
	t.Helper()
	if !cond {
		t.Fatalf(format, args...)
	}
}

type scripted struct {
	status int
	header map[string]string
	body   any
}

type captured struct {
	method string
	path   string
	auth   string
	ctype  string
	body   map[string]any
}

type fakeMeraki struct {
	mu       sync.Mutex
	requests []captured
	queues   map[string][]scripted
	srv      *httptest.Server
}

func newFake(t *testing.T) *fakeMeraki {
	f := &fakeMeraki{queues: map[string][]scripted{}}
	f.srv = httptest.NewServer(http.HandlerFunc(f.handle))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fakeMeraki) baseURL() string { return f.srv.URL + "/api/v1" }

func (f *fakeMeraki) enqueue(method, path string, status int, header map[string]string, body any) {
	f.mu.Lock()
	defer f.mu.Unlock()
	key := method + " " + path
	f.queues[key] = append(f.queues[key], scripted{status, header, body})
}

func (f *fakeMeraki) counts(method, path string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := 0
	for _, r := range f.requests {
		if r.method == method && r.path == path {
			n++
		}
	}
	return n
}

func (f *fakeMeraki) handle(w http.ResponseWriter, r *http.Request) {
	var body map[string]any
	if r.Body != nil {
		raw, _ := io.ReadAll(r.Body)
		if len(raw) > 0 {
			_ = json.Unmarshal(raw, &body)
		}
	}
	f.mu.Lock()
	f.requests = append(f.requests, captured{
		method: r.Method,
		path:   r.URL.Path,
		auth:   r.Header.Get("Authorization"),
		ctype:  r.Header.Get("Content-Type"),
		body:   body,
	})
	key := r.Method + " " + r.URL.Path
	queue := f.queues[key]
	var resp scripted
	if len(queue) == 0 {
		resp = scripted{500, nil, map[string]any{"errors": []string{"unexpected request " + key}}}
	} else {
		resp = queue[0]
		f.queues[key] = queue[1:]
	}
	f.mu.Unlock()
	for k, v := range resp.header {
		w.Header().Set(k, v)
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.status)
	_ = json.NewEncoder(w).Encode(resp.body)
}

func makeActions(n int) []mbatch.Action {
	actions := make([]mbatch.Action, n)
	for i := range actions {
		actions[i] = mbatch.Action{
			Resource:  fmt.Sprintf("/networks/N_%03d/vlans", i),
			Operation: "create",
			Body:      map[string]any{"id": 100 + i, "name": fmt.Sprintf("vlan-%03d", i)},
		}
	}
	return actions
}

func batchBody(id string, completed, failed bool, errs []string, created []mbatch.CreatedResource) map[string]any {
	if errs == nil {
		errs = []string{}
	}
	cr := make([]any, 0, len(created))
	for _, c := range created {
		cr = append(cr, map[string]any{"id": c.ID, "uri": c.URI})
	}
	return map[string]any{
		"id":             id,
		"organizationId": orgID,
		"confirmed":      true,
		"synchronous":    false,
		"status": map[string]any{
			"completed":        completed,
			"failed":           failed,
			"errors":           errs,
			"createdResources": cr,
		},
		"actions": []any{},
	}
}

const batchesPath = "/api/v1/organizations/" + orgID + "/actionBatches"

func TestSplitActionsHonorsDocumentedLimits(t *testing.T) {
	actions := makeActions(250)
	async := mbatch.SplitActions(actions, false)
	check(t, len(async) == 3, "250 async actions must split into 3 batches, got %d", len(async))
	check(t, len(async[0]) == 100 && len(async[1]) == 100 && len(async[2]) == 50,
		"async batches must be 100/100/50, got %d/%d/%d", len(async[0]), len(async[1]), len(async[2]))
	check(t, async[1][0].Resource == actions[100].Resource, "order must be preserved across chunks")
	check(t, async[2][49].Resource == actions[249].Resource, "the final action must land last")

	sync := mbatch.SplitActions(makeActions(45), true)
	check(t, len(sync) == 3, "45 sync actions must split into 3 batches, got %d", len(sync))
	check(t, len(sync[0]) == 20 && len(sync[1]) == 20 && len(sync[2]) == 5,
		"sync batches must be 20/20/5, got %d/%d/%d", len(sync[0]), len(sync[1]), len(sync[2]))

	exact := mbatch.SplitActions(makeActions(20), true)
	check(t, len(exact) == 1 && len(exact[0]) == 20, "exactly 20 sync actions is one full batch")
	check(t, len(mbatch.SplitActions(nil, true)) == 0, "no actions means no batches")
}

func TestCreateActionBatchWireFormat(t *testing.T) {
	fake := newFake(t)
	fake.enqueue("POST", batchesPath, 201,
		nil, batchBody("B1", true, false, nil, []mbatch.CreatedResource{{ID: "7001", URI: "/networks/N_000/vlans/100"}}))

	client := mbatch.NewClient(fake.baseURL(), apiKey, fake.srv.Client(), func(time.Duration) {})
	actions := []mbatch.Action{
		{Resource: "/networks/N_000/vlans", Operation: "create", Body: map[string]any{"id": 100, "name": "vlan-000"}},
		{Resource: "/networks/N_001/ssids/3", Operation: "update"},
	}
	batch, err := client.CreateActionBatch(context.Background(), orgID, true, true, actions)
	check(t, err == nil, "create failed: %v", err)
	check(t, batch.ID == "B1", "batch id must be decoded, got %q", batch.ID)
	check(t, batch.Status.Completed && !batch.Status.Failed, "status flags must be decoded")
	check(t, len(batch.Status.CreatedResources) == 1 && batch.Status.CreatedResources[0].URI == "/networks/N_000/vlans/100",
		"createdResources must be decoded")

	req := fake.requests[0]
	check(t, req.method == "POST" && req.path == batchesPath,
		"create must POST the documented organizations actionBatches path, got %s %s", req.method, req.path)
	check(t, req.auth == "Bearer "+apiKey, "documented bearer header missing, got %q", req.auth)
	check(t, strings.HasPrefix(req.ctype, "application/json"), "JSON content type required, got %q", req.ctype)
	check(t, req.body["confirmed"] == true, "confirmed must be an explicit body field")
	check(t, req.body["synchronous"] == true, "synchronous must be an explicit body field")
	sent, ok := req.body["actions"].([]any)
	check(t, ok && len(sent) == 2, "actions array must carry both actions")
	first, ok := sent[0].(map[string]any)
	check(t, ok && first["resource"] == "/networks/N_000/vlans" && first["operation"] == "create",
		"action resource/operation must use the documented field names")
	_, hasBody := first["body"]
	check(t, hasBody, "an action with a body must send it")
	second, ok := sent[1].(map[string]any)
	check(t, ok, "second action must be an object")
	_, hasBody = second["body"]
	check(t, !hasBody, "an action without a body must omit the body key entirely")
}

func TestRunSynchronousBatches(t *testing.T) {
	fake := newFake(t)
	fake.enqueue("POST", batchesPath, 201,
		nil, batchBody("S1", true, false, nil, []mbatch.CreatedResource{{ID: "7101", URI: "/networks/N_000/vlans/100"}}))
	fake.enqueue("POST", batchesPath, 201,
		nil, batchBody("S2", true, false, nil, []mbatch.CreatedResource{{ID: "7102", URI: "/networks/N_020/vlans/120"}}))

	var pace []int
	client := mbatch.NewClient(fake.baseURL(), apiKey, fake.srv.Client(), func(time.Duration) {})
	runner := &mbatch.Runner{Client: client, Pace: func(a int) { pace = append(pace, a) }, MaxPolls: 5}
	report, err := runner.Run(context.Background(), orgID, makeActions(25), true)
	check(t, err == nil, "sync run failed: %v", err)
	check(t, report.SubmittedBatches == 2 && report.CompletedBatches == 2,
		"25 sync actions is 2 submitted+completed batches, got %d/%d", report.SubmittedBatches, report.CompletedBatches)
	check(t, len(report.BatchIDs) == 2 && report.BatchIDs[0] == "S1" && report.BatchIDs[1] == "S2",
		"batch ids must be recorded in submission order, got %v", report.BatchIDs)
	check(t, report.Polls == 0, "a synchronous 201 with terminal status needs no polling, got %d polls", report.Polls)
	check(t, len(pace) == 0, "no pacing without polling")
	check(t, len(report.CreatedResources) == 2 && report.CreatedResources[0].ID == "7101" && report.CreatedResources[1].ID == "7102",
		"created resources must accumulate in order, got %v", report.CreatedResources)

	check(t, fake.counts("POST", batchesPath) == 2, "exactly 2 batch submissions")
	firstActions := fake.requests[0].body["actions"].([]any)
	secondActions := fake.requests[1].body["actions"].([]any)
	check(t, len(firstActions) == 20 && len(secondActions) == 5,
		"sync submissions must respect the 20-action limit, got %d/%d", len(firstActions), len(secondActions))
	check(t, fake.requests[0].body["synchronous"] == true && fake.requests[1].body["synchronous"] == true,
		"sync mode must be flagged on every submission")
	check(t, fake.requests[0].body["confirmed"] == true && fake.requests[1].body["confirmed"] == true,
		"batches must be submitted confirmed for immediate execution")
}

func TestRunAsynchronousPolling(t *testing.T) {
	fake := newFake(t)
	fake.enqueue("POST", batchesPath, 201, nil, batchBody("B-A1", false, false, nil, nil))
	fake.enqueue("GET", batchesPath+"/B-A1", 200, nil, batchBody("B-A1", false, false, nil, nil))
	fake.enqueue("GET", batchesPath+"/B-A1", 200, nil,
		batchBody("B-A1", true, false, nil, []mbatch.CreatedResource{
			{ID: "7201", URI: "/networks/N_000/vlans/100"},
			{ID: "7202", URI: "/networks/N_001/vlans/101"},
		}))
	fake.enqueue("POST", batchesPath, 201, nil, batchBody("B-A2", false, false, nil, nil))
	fake.enqueue("GET", batchesPath+"/B-A2", 200, nil,
		batchBody("B-A2", true, false, nil, []mbatch.CreatedResource{{ID: "7203", URI: "/networks/N_100/vlans/200"}}))

	var pace []int
	client := mbatch.NewClient(fake.baseURL(), apiKey, fake.srv.Client(), func(time.Duration) {})
	runner := &mbatch.Runner{Client: client, Pace: func(a int) { pace = append(pace, a) }, MaxPolls: 5}
	report, err := runner.Run(context.Background(), orgID, makeActions(120), false)
	check(t, err == nil, "async run failed: %v", err)
	check(t, fake.counts("POST", batchesPath) == 2, "120 async actions is 2 submissions")
	asyncFirst := fake.requests[0].body["actions"].([]any)
	check(t, len(asyncFirst) == 100, "async submissions may carry up to 100 actions, got %d", len(asyncFirst))
	check(t, fake.requests[0].body["synchronous"] == false, "async mode must not set synchronous")
	check(t, report.Polls == 3, "two polls for B-A1 plus one for B-A2, got %d", report.Polls)
	check(t, len(pace) == 3 && pace[0] == 1 && pace[1] == 2 && pace[2] == 1,
		"pace attempts must be 1-based per batch, got %v", pace)
	check(t, report.CompletedBatches == 2, "both batches completed, got %d", report.CompletedBatches)
	check(t, len(report.CreatedResources) == 3 && report.CreatedResources[2].ID == "7203",
		"created resources from every batch must be preserved in order, got %v", report.CreatedResources)
}

func TestRunPreservesSuccessesWhenLaterBatchFails(t *testing.T) {
	fake := newFake(t)
	fake.enqueue("POST", batchesPath, 201, nil,
		batchBody("B1", true, false, nil, []mbatch.CreatedResource{
			{ID: "7301", URI: "/networks/N_000/vlans/100"},
			{ID: "7302", URI: "/networks/N_001/vlans/101"},
		}))
	fake.enqueue("POST", batchesPath, 201, nil, batchBody("B2", false, false, nil, nil))
	failMsg := "Vlan create failed for /networks/N_107/vlans: id already exists"
	fake.enqueue("GET", batchesPath+"/B2", 200, nil, batchBody("B2", false, true, []string{failMsg}, nil))

	client := mbatch.NewClient(fake.baseURL(), apiKey, fake.srv.Client(), func(time.Duration) {})
	runner := &mbatch.Runner{Client: client, Pace: func(int) {}, MaxPolls: 5}
	report, err := runner.Run(context.Background(), orgID, makeActions(250), false)
	check(t, err != nil, "a failed batch must surface an error")
	check(t, strings.Contains(err.Error(), "B2"), "the error must name the failed batch, got %v", err)
	check(t, fake.counts("POST", batchesPath) == 2,
		"the third batch must NOT be submitted after a failure, got %d submissions", fake.counts("POST", batchesPath))
	check(t, report != nil, "a partial report must be returned on failure")
	check(t, report.CompletedBatches == 1, "the first batch's success must be preserved, got %d", report.CompletedBatches)
	check(t, len(report.CreatedResources) == 2 && report.CreatedResources[0].ID == "7301",
		"successful created resources must be preserved, got %v", report.CreatedResources)
	check(t, report.FailedBatchID == "B2", "failed batch id must be recorded, got %q", report.FailedBatchID)
	check(t, len(report.FailedActionErrors) == 1, "one status error means one correlated action error, got %v", report.FailedActionErrors)
	ae := report.FailedActionErrors[0]
	check(t, ae.BatchID == "B2", "action error must carry the batch id, got %q", ae.BatchID)
	check(t, ae.Resource == "/networks/N_107/vlans",
		"the error must be correlated to the action whose resource it names, got %q", ae.Resource)
	check(t, ae.ActionIndex == 7, "global action 107 is index 7 within batch 2, got %d", ae.ActionIndex)
	check(t, ae.Message == failMsg, "the server's error string must be preserved verbatim, got %q", ae.Message)
}

func TestRateLimitRetryHonorsRetryAfter(t *testing.T) {
	fake := newFake(t)
	fake.enqueue("POST", batchesPath, 429,
		map[string]string{"Retry-After": "2"},
		map[string]any{"errors": []string{"API rate limit exceeded for organization"}})
	fake.enqueue("POST", batchesPath, 201,
		nil, batchBody("R1", true, false, nil, nil))

	var sleeps []time.Duration
	client := mbatch.NewClient(fake.baseURL(), apiKey, fake.srv.Client(), func(d time.Duration) { sleeps = append(sleeps, d) })
	runner := &mbatch.Runner{Client: client, Pace: func(int) {}, MaxPolls: 5}
	report, err := runner.Run(context.Background(), orgID, makeActions(5), true)
	check(t, err == nil, "run must succeed after honoring Retry-After: %v", err)
	check(t, len(sleeps) == 1 && sleeps[0] == 2*time.Second,
		"must wait exactly the Retry-After seconds via the injected sleep, got %v", sleeps)
	check(t, fake.counts("POST", batchesPath) == 2, "one retry after the 429")
	check(t, report.Throttled == 1, "throttle waits must be counted, got %d", report.Throttled)
	check(t, report.CompletedBatches == 1, "batch must complete after the retry")
}

func TestPollingIsBounded(t *testing.T) {
	fake := newFake(t)
	fake.enqueue("POST", batchesPath, 201, nil, batchBody("B-P", false, false, nil, nil))
	for range 3 {
		fake.enqueue("GET", batchesPath+"/B-P", 200, nil, batchBody("B-P", false, false, nil, nil))
	}
	client := mbatch.NewClient(fake.baseURL(), apiKey, fake.srv.Client(), func(time.Duration) {})
	runner := &mbatch.Runner{Client: client, Pace: func(int) {}, MaxPolls: 3}
	report, err := runner.Run(context.Background(), orgID, makeActions(5), false)
	check(t, err != nil, "exhausting MaxPolls must surface an error")
	check(t, strings.Contains(err.Error(), "3"), "the error must mention the poll bound, got %v", err)
	check(t, report != nil && report.Polls == 3, "exactly MaxPolls polls, got %+v", report)
	check(t, len(report.BatchIDs) == 1 && report.BatchIDs[0] == "B-P",
		"the stuck batch id must be in the partial report, got %v", report.BatchIDs)
}

func TestAPIErrorDecodingAndRedaction(t *testing.T) {
	fake := newFake(t)
	fake.enqueue("GET", batchesPath+"/missing", 404, nil,
		map[string]any{"errors": []string{"Organization not found"}})
	client := mbatch.NewClient(fake.baseURL(), apiKey, fake.srv.Client(), func(time.Duration) {})
	_, err := client.GetActionBatch(context.Background(), orgID, "missing")
	check(t, err != nil, "404 must surface an error")
	var apiErr *mbatch.APIError
	check(t, errors.As(err, &apiErr), "error must be an *APIError, got %T", err)
	check(t, apiErr.Status == 404, "status must be carried, got %d", apiErr.Status)
	check(t, len(apiErr.Errors) == 1 && apiErr.Errors[0] == "Organization not found",
		"documented errors array must be decoded, got %v", apiErr.Errors)
	check(t, strings.Contains(err.Error(), "404"), "message must name the status")
	check(t, !strings.Contains(err.Error(), apiKey), "the API key must never leak into errors")
	check(t, fake.requests[0].auth == "Bearer "+apiKey, "GET must carry the bearer header too")
}
