// Acceptance harness for the glpipe package: a loopback fake GitLab REST v4
// API exercising the pipeline create/watch wire contract pinned in
// docs/contract.json. No real GitLab, no real credentials, no sleeps.
// Protected — do not modify. Run: go test -race -timeout 30s ./...
package glpipe_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	glpipe "go-gitlab-pipeline-watcher"
)

const (
	token     = "glpat-dummy-w4tcher-7c31"
	projectID = 4711
	pipeID    = 88123
)

type captured struct {
	Method string
	Path   string
	Query  map[string]string
	Token  string
	Body   map[string]any
}

type fakeGitLab struct {
	mu        sync.Mutex
	requests  []captured
	pollBody  []map[string]any // successive GET pipeline responses
	pollHdr   []map[string]string
	pollIdx   int
	jobsPages [][]map[string]any
	traces    map[int]string
	srv       *httptest.Server
}

func newFake(t *testing.T) *fakeGitLab {
	f := &fakeGitLab{traces: map[int]string{}}
	f.srv = httptest.NewServer(http.HandlerFunc(f.handle))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fakeGitLab) record(r *http.Request) captured {
	q := map[string]string{}
	for k, v := range r.URL.Query() {
		q[k] = v[0]
	}
	var body map[string]any
	if r.Body != nil {
		_ = json.NewDecoder(r.Body).Decode(&body)
	}
	c := captured{
		Method: r.Method,
		Path:   r.URL.Path,
		Query:  q,
		Token:  r.Header.Get("PRIVATE-TOKEN"),
		Body:   body,
	}
	f.requests = append(f.requests, c)
	return c
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func (f *fakeGitLab) handle(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	c := f.record(r)

	createPath := fmt.Sprintf("/api/v4/projects/%d/pipeline", projectID)
	getPath := fmt.Sprintf("/api/v4/projects/%d/pipelines/%d", projectID, pipeID)
	jobsPath := getPath + "/jobs"
	cancelPath := getPath + "/cancel"

	switch {
	case c.Method == "POST" && c.Path == createPath:
		writeJSON(w, 201, map[string]any{
			"id": pipeID, "iid": 412, "project_id": projectID,
			"status": "created", "source": "api", "ref": c.Body["ref"],
			"sha":     "6104942438c14ec7bd21c6cd5bd995272b3faff6",
			"web_url": "https://gitlab.example.com/ops/deployer/-/pipelines/88123",
		})
	case c.Method == "GET" && c.Path == getPath:
		i := f.pollIdx
		if i >= len(f.pollBody) {
			i = len(f.pollBody) - 1
		}
		f.pollIdx++
		hdr := map[string]string{}
		if i < len(f.pollHdr) && f.pollHdr[i] != nil {
			hdr = f.pollHdr[i]
		}
		for k, v := range hdr {
			w.Header().Set(k, v)
		}
		body := f.pollBody[i]
		if body == nil { // scripted 429
			w.Header().Set("Content-Type", "text/plain")
			w.WriteHeader(429)
			_, _ = w.Write([]byte("Retry later"))
			return
		}
		writeJSON(w, 200, body)
	case c.Method == "GET" && c.Path == jobsPath:
		page := 1
		if p := c.Query["page"]; p != "" {
			page, _ = strconv.Atoi(p)
		}
		if page > len(f.jobsPages) {
			writeJSON(w, 200, []any{})
			return
		}
		next := ""
		if page < len(f.jobsPages) {
			next = strconv.Itoa(page + 1)
		}
		w.Header().Set("x-page", strconv.Itoa(page))
		w.Header().Set("x-per-page", c.Query["per_page"])
		w.Header().Set("x-total-pages", strconv.Itoa(len(f.jobsPages)))
		w.Header().Set("x-next-page", next)
		writeJSON(w, 200, f.jobsPages[page-1])
	case c.Method == "POST" && c.Path == cancelPath:
		writeJSON(w, 200, map[string]any{"id": pipeID, "status": "canceled"})
	case c.Method == "GET" && strings.HasSuffix(c.Path, "/trace"):
		var jobID int
		_, _ = fmt.Sscanf(c.Path, "/api/v4/projects/4711/jobs/%d/trace", &jobID)
		trace, ok := f.traces[jobID]
		if !ok {
			writeJSON(w, 404, map[string]any{"message": "404 Not Found"})
			return
		}
		w.Header().Set("Content-Type", "text/plain")
		w.WriteHeader(200)
		_, _ = w.Write([]byte(trace))
	default:
		writeJSON(w, 404, map[string]any{"message": "404 Not Found"})
	}
}

func status(s string) map[string]any {
	return map[string]any{
		"id": pipeID, "iid": 412, "project_id": projectID,
		"status": s, "source": "api", "ref": "main",
		"sha":     "6104942438c14ec7bd21c6cd5bd995272b3faff6",
		"web_url": "https://gitlab.example.com/ops/deployer/-/pipelines/88123",
	}
}

func job(id int, name, stage, jstatus string) map[string]any {
	return map[string]any{
		"id": id, "name": name, "stage": stage, "status": jstatus,
		"ref": "main", "allow_failure": false,
		"web_url": fmt.Sprintf("https://gitlab.example.com/ops/deployer/-/jobs/%d", id),
	}
}

func newClient(f *fakeGitLab, sleeps *[]time.Duration) *glpipe.Client {
	return glpipe.NewClient(f.srv.URL, token, f.srv.Client(), func(d time.Duration) {
		*sleeps = append(*sleeps, d)
	})
}

func deployVars() []glpipe.Variable {
	return []glpipe.Variable{
		{Key: "DEPLOY_ENV", Value: "staging"},
		{Key: "RELEASE_MANIFEST", Value: "manifests/rel-2026.31.yaml", VariableType: "file"},
	}
}

func TestCreatePipelineWireFormat(t *testing.T) {
	f := newFake(t)
	var sleeps []time.Duration
	c := newClient(f, &sleeps)

	p, err := c.CreatePipeline(context.Background(), projectID, "main", deployVars())
	if err != nil {
		t.Fatalf("CreatePipeline: %v", err)
	}
	if p.ID != pipeID || p.Status != "created" || p.Ref != "main" {
		t.Fatalf("bad pipeline decode: %+v", p)
	}
	if p.WebURL == "" || p.SHA == "" {
		t.Fatalf("web_url/sha must be decoded: %+v", p)
	}

	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.requests) != 1 {
		t.Fatalf("expected exactly one request, got %d", len(f.requests))
	}
	req := f.requests[0]
	if req.Method != "POST" {
		t.Fatalf("create must POST, got %s", req.Method)
	}
	if req.Path != "/api/v4/projects/4711/pipeline" {
		t.Fatalf("wrong create path %q (singular 'pipeline' under /api/v4)", req.Path)
	}
	if req.Token != token {
		t.Fatalf("PRIVATE-TOKEN header missing or wrong: %q", req.Token)
	}
	if req.Body["ref"] != "main" {
		t.Fatalf("body ref = %v", req.Body["ref"])
	}
	vars, ok := req.Body["variables"].([]any)
	if !ok || len(vars) != 2 {
		t.Fatalf("body variables = %v", req.Body["variables"])
	}
	first := vars[0].(map[string]any)
	if first["key"] != "DEPLOY_ENV" || first["value"] != "staging" {
		t.Fatalf("first variable = %v", first)
	}
	if _, present := first["variable_type"]; present {
		t.Fatalf("variable_type must be omitted when unset (defaults to env_var server-side): %v", first)
	}
	second := vars[1].(map[string]any)
	if second["variable_type"] != "file" {
		t.Fatalf("second variable must carry variable_type=file: %v", second)
	}
}

func TestCreatePipelineDecodesValidationError(t *testing.T) {
	f := newFake(t)
	var sleeps []time.Duration
	c := newClient(f, &sleeps)

	// Unknown project → the fake answers GitLab's 404 message document.
	_, err := c.CreatePipeline(context.Background(), 999, "main", nil)
	if err == nil {
		t.Fatal("expected an error for an unknown project")
	}
	if !strings.Contains(err.Error(), "404") {
		t.Fatalf("error must carry the HTTP status: %v", err)
	}
	if strings.Contains(err.Error(), token) {
		t.Fatalf("token leaked into error: %v", err)
	}
}

func TestWatchRunsToSuccessAndCollectsFailedTraces(t *testing.T) {
	f := newFake(t)
	f.pollBody = []map[string]any{status("running"), status("running"), status("success")}
	f.jobsPages = [][]map[string]any{
		{job(9001, "assemble", "build", "success"), job(9002, "unit-suite", "test", "failed")},
		{job(9003, "lint", "test", "success")},
	}
	f.traces = map[int]string{9002: "FAIL: TestCheckoutTotals (0.03s)\n--- expected 41.90, got 41.00\n"}

	var sleeps []time.Duration
	var paced []int
	c := newClient(f, &sleeps)
	w := &glpipe.Watcher{Client: c, PerPage: 2, MaxPolls: 25, Pace: func(a int) { paced = append(paced, a) }}

	rep, err := w.Run(context.Background(), projectID, "main", deployVars())
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if !rep.Completed || rep.Status != "success" || rep.PipelineID != pipeID {
		t.Fatalf("bad report: %+v", rep)
	}
	if rep.Polls != 3 {
		t.Fatalf("expected 3 status polls, got %d", rep.Polls)
	}
	if len(paced) != 2 || paced[0] != 1 || paced[1] != 2 {
		t.Fatalf("Pace must run between consecutive polls with 1-based attempts, got %v", paced)
	}
	if len(sleeps) != 0 {
		t.Fatalf("no throttling happened; sleep must not be called: %v", sleeps)
	}
	if len(rep.Jobs) != 3 {
		t.Fatalf("jobs must be collected across pages: %+v", rep.Jobs)
	}
	if rep.Jobs[0].Name != "assemble" || rep.Jobs[2].Name != "lint" {
		t.Fatalf("job order must follow the pages: %+v", rep.Jobs)
	}
	if rep.Jobs[1].Status != "failed" || rep.Jobs[1].Stage != "test" {
		t.Fatalf("job fields must decode: %+v", rep.Jobs[1])
	}
	want := f.traces[9002]
	if got := rep.FailedTraces[9002]; got != want {
		t.Fatalf("failed trace mismatch: %q", got)
	}
	if len(rep.FailedTraces) != 1 {
		t.Fatalf("traces must be fetched only for failed jobs: %v", rep.FailedTraces)
	}

	f.mu.Lock()
	defer f.mu.Unlock()
	var jobCalls []captured
	traceCalls := 0
	for _, r := range f.requests {
		if r.Token != token {
			t.Fatalf("every API request needs PRIVATE-TOKEN, missing on %s %s", r.Method, r.Path)
		}
		if strings.HasSuffix(r.Path, "/jobs") {
			jobCalls = append(jobCalls, r)
		}
		if strings.HasSuffix(r.Path, "/trace") {
			traceCalls++
		}
	}
	if len(jobCalls) != 2 {
		t.Fatalf("expected 2 job-page requests, got %d", len(jobCalls))
	}
	if jobCalls[0].Query["per_page"] != "2" {
		t.Fatalf("first jobs call must send per_page=2: %v", jobCalls[0].Query)
	}
	if jobCalls[1].Query["page"] != "2" {
		t.Fatalf("second jobs call must follow x-next-page to page=2: %v", jobCalls[1].Query)
	}
	if traceCalls != 1 {
		t.Fatalf("only the failed job's trace may be downloaded, saw %d", traceCalls)
	}
}

func TestCancelingIsNotTerminal(t *testing.T) {
	f := newFake(t)
	f.pollBody = []map[string]any{status("canceling"), status("canceled")}
	f.jobsPages = [][]map[string]any{{job(9001, "assemble", "build", "canceled")}}

	var sleeps []time.Duration
	c := newClient(f, &sleeps)
	w := &glpipe.Watcher{Client: c, PerPage: 100, MaxPolls: 10, Pace: func(int) {}}
	rep, err := w.Run(context.Background(), projectID, "main", nil)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if rep.Status != "canceled" || !rep.Completed {
		t.Fatalf("canceled is terminal: %+v", rep)
	}
	if rep.Polls != 2 {
		t.Fatalf("canceling must be polled through, not treated as terminal: %d polls", rep.Polls)
	}
}

func TestRetryAfterIsHonored(t *testing.T) {
	f := newFake(t)
	f.pollBody = []map[string]any{nil, status("running"), status("success")}
	f.pollHdr = []map[string]string{{
		"RateLimit-Limit":     "60",
		"RateLimit-Remaining": "0",
		"RateLimit-Reset":     "1784629800",
		"Retry-After":         "30",
	}}
	f.jobsPages = [][]map[string]any{{job(9001, "assemble", "build", "success")}}

	var sleeps []time.Duration
	var paced []int
	c := newClient(f, &sleeps)
	w := &glpipe.Watcher{Client: c, PerPage: 100, MaxPolls: 10, Pace: func(a int) { paced = append(paced, a) }}
	rep, err := w.Run(context.Background(), projectID, "main", nil)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if len(sleeps) != 1 || sleeps[0] != 30*time.Second {
		t.Fatalf("a 429 must wait exactly Retry-After seconds via the injected sleep, got %v", sleeps)
	}
	if rep.Throttled != 1 {
		t.Fatalf("report must count throttle events: %+v", rep)
	}
	if !rep.Completed || rep.Status != "success" {
		t.Fatalf("watch must recover after the throttled poll: %+v", rep)
	}
	if rep.Polls != 2 {
		t.Fatalf("the 429 response is not a successful poll; expected 2 polls, got %d", rep.Polls)
	}
}

func TestPersistent429GivesUpBounded(t *testing.T) {
	f := newFake(t)
	f.pollBody = []map[string]any{nil}
	f.pollHdr = []map[string]string{{"Retry-After": "1", "RateLimit-Remaining": "0"}}

	var sleeps []time.Duration
	c := newClient(f, &sleeps)
	w := &glpipe.Watcher{Client: c, PerPage: 100, MaxPolls: 5, Pace: func(int) {}}
	_, err := w.Run(context.Background(), projectID, "main", nil)
	if err == nil {
		t.Fatal("persistent 429 must surface an error")
	}
	if !strings.Contains(err.Error(), "429") {
		t.Fatalf("error must mention the 429 status: %v", err)
	}
	if strings.Contains(err.Error(), token) {
		t.Fatalf("token leaked into error: %v", err)
	}
	f.mu.Lock()
	n := len(f.requests)
	f.mu.Unlock()
	if n > 12 {
		t.Fatalf("429 retries must be bounded, saw %d requests", n)
	}
	if len(sleeps) == 0 {
		t.Fatal("Retry-After waits must go through the injected sleep")
	}
}

func TestCancellationReturnsPartialReport(t *testing.T) {
	f := newFake(t)
	f.pollBody = []map[string]any{status("running")}

	ctx, cancel := context.WithCancel(context.Background())
	var sleeps []time.Duration
	c := newClient(f, &sleeps)
	w := &glpipe.Watcher{Client: c, PerPage: 100, MaxPolls: 50, Pace: func(a int) {
		if a == 3 {
			cancel()
		}
	}}
	rep, err := w.Run(ctx, projectID, "main", deployVars())
	if err == nil || !errors.Is(err, context.Canceled) {
		t.Fatalf("cancellation must surface context.Canceled, got %v", err)
	}
	if rep == nil {
		t.Fatal("cancellation must still return the partial report")
	}
	if rep.Completed {
		t.Fatalf("partial report cannot claim completion: %+v", rep)
	}
	if rep.PipelineID != pipeID || rep.Status != "running" {
		t.Fatalf("partial report must carry the last observed state: %+v", rep)
	}
	if rep.Polls != 3 {
		t.Fatalf("expected 3 polls before cancellation, got %d", rep.Polls)
	}
	if !rep.CancelRequested {
		t.Fatalf("watcher must request a server-side cancel: %+v", rep)
	}

	f.mu.Lock()
	defer f.mu.Unlock()
	cancels := 0
	for _, r := range f.requests {
		if r.Method == "POST" && strings.HasSuffix(r.Path, "/pipelines/88123/cancel") {
			cancels++
			if r.Token != token {
				t.Fatal("cancel POST must authenticate")
			}
		}
	}
	if cancels != 1 {
		t.Fatalf("exactly one POST .../cancel expected, got %d", cancels)
	}
}

func TestPollBudgetExhausted(t *testing.T) {
	f := newFake(t)
	f.pollBody = []map[string]any{status("running")}

	var sleeps []time.Duration
	c := newClient(f, &sleeps)
	w := &glpipe.Watcher{Client: c, PerPage: 100, MaxPolls: 4, Pace: func(int) {}}
	rep, err := w.Run(context.Background(), projectID, "main", nil)
	if err == nil {
		t.Fatal("exhausting MaxPolls must surface an error")
	}
	if rep == nil || rep.Completed || rep.Polls != 4 {
		t.Fatalf("partial report expected after 4 polls: %+v", rep)
	}
}
