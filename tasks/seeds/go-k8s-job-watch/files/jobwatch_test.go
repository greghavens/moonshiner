// Acceptance harness for the jobwatch package: a loopback fake Kubernetes API
// server speaking the batch/v1 list+watch protocol pinned in docs/contract.json.
// No real cluster, no real credentials. Protected — do not modify.
package jobwatch_test

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	watch "go-k8s-job-watch"
)

const token = "dummy-token-8f2a71" // dummy; must never leak into errors

// ---------------------------------------------------------------- fake API

type reqRec struct {
	method string
	path   string
	query  url.Values
	auth   string
	accept string
}

type listScript struct {
	status int
	body   string
}

type watchScript struct {
	status     int    // non-zero and non-200: written with statusBody
	statusBody string
	frames     []string // JSON frames, each written + "\n" and flushed
	splitFirst bool     // deliver the first frame across two flushes
	hold       bool     // keep the stream open until the client disconnects
}

type fakeAPI struct {
	t  *testing.T
	mu sync.Mutex

	lists   []listScript
	watches []watchScript
	reqs    []reqRec

	listStarted  chan url.Values
	watchStarted chan url.Values
	disconnected chan struct{}

	srv *httptest.Server
}

func newFake(t *testing.T) *fakeAPI {
	f := &fakeAPI{
		t:            t,
		listStarted:  make(chan url.Values, 16),
		watchStarted: make(chan url.Values, 16),
		disconnected: make(chan struct{}, 16),
	}
	f.srv = httptest.NewServer(http.HandlerFunc(f.handle))
	t.Cleanup(f.srv.Close)
	return f
}

func (f *fakeAPI) recorded() []reqRec {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]reqRec, len(f.reqs))
	copy(out, f.reqs)
	return out
}

func (f *fakeAPI) handle(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	f.mu.Lock()
	f.reqs = append(f.reqs, reqRec{r.Method, r.URL.Path, q,
		r.Header.Get("Authorization"), r.Header.Get("Accept")})
	f.mu.Unlock()

	w.Header().Set("Content-Type", "application/json")
	if r.URL.Path != "/apis/batch/v1/namespaces/batch-jobs/jobs" {
		w.WriteHeader(404)
		fmt.Fprint(w, statusJSON(404, "NotFound", "unknown path "+r.URL.Path))
		return
	}

	if q.Get("watch") == "true" || q.Get("watch") == "1" {
		f.mu.Lock()
		if len(f.watches) == 0 {
			f.mu.Unlock()
			w.WriteHeader(500)
			fmt.Fprint(w, statusJSON(500, "InternalError", "unscripted watch request"))
			return
		}
		ws := f.watches[0]
		f.watches = f.watches[1:]
		f.mu.Unlock()
		f.watchStarted <- q

		if ws.status != 0 && ws.status != 200 {
			w.WriteHeader(ws.status)
			fmt.Fprint(w, ws.statusBody)
			return
		}
		w.WriteHeader(200)
		fl := w.(http.Flusher)
		for i, fr := range ws.frames {
			if i == 0 && ws.splitFirst && len(fr) > 10 {
				fmt.Fprint(w, fr[:10])
				fl.Flush()
				fmt.Fprint(w, fr[10:]+"\n")
				fl.Flush()
				continue
			}
			fmt.Fprint(w, fr+"\n")
			fl.Flush()
		}
		if ws.hold {
			<-r.Context().Done()
			f.disconnected <- struct{}{}
		}
		return
	}

	f.mu.Lock()
	if len(f.lists) == 0 {
		f.mu.Unlock()
		w.WriteHeader(500)
		fmt.Fprint(w, statusJSON(500, "InternalError", "unscripted list request"))
		return
	}
	ls := f.lists[0]
	f.lists = f.lists[1:]
	f.mu.Unlock()
	f.listStarted <- q
	w.WriteHeader(ls.status)
	fmt.Fprint(w, ls.body)
}

// ---------------------------------------------------------------- builders

func statusJSON(code int, reason, message string) string {
	return fmt.Sprintf(`{"kind":"Status","apiVersion":"v1","status":"Failure","message":%q,"reason":%q,"code":%d}`,
		message, reason, code)
}

func cond(ctype, status, reason string) string {
	return fmt.Sprintf(`{"type":%q,"status":%q,"reason":%q,"message":"synthetic %s"}`,
		ctype, status, reason, ctype)
}

func jobJSON(name, uid, rv string, active, succeeded, failed int, conds ...string) string {
	return fmt.Sprintf(`{"apiVersion":"batch/v1","kind":"Job","metadata":{"name":%q,"namespace":"batch-jobs","uid":%q,"resourceVersion":%q},"status":{"active":%d,"succeeded":%d,"failed":%d,"conditions":[%s]}}`,
		name, uid, rv, active, succeeded, failed, strings.Join(conds, ","))
}

func listJSON(rv string, jobs ...string) string {
	return fmt.Sprintf(`{"kind":"JobList","apiVersion":"batch/v1","metadata":{"resourceVersion":%q},"items":[%s]}`,
		rv, strings.Join(jobs, ","))
}

func frame(eventType, object string) string {
	return fmt.Sprintf(`{"type":%q,"object":%s}`, eventType, object)
}

func bookmarkFrame(rv string) string {
	return frame("BOOKMARK",
		fmt.Sprintf(`{"apiVersion":"batch/v1","kind":"Job","metadata":{"resourceVersion":%q}}`, rv))
}

// ---------------------------------------------------------------- helpers

func newClient(f *fakeAPI) *watch.Client {
	return watch.NewClient(f.srv.URL, token, f.srv.Client())
}

func recvValues(t *testing.T, ch <-chan url.Values, what string) url.Values {
	t.Helper()
	select {
	case v := <-ch:
		return v
	case <-time.After(5 * time.Second):
		t.Fatalf("timed out waiting for %s", what)
	}
	return nil
}

func recvErr(t *testing.T, ch <-chan error, what string) error {
	t.Helper()
	select {
	case v := <-ch:
		return v
	case <-time.After(5 * time.Second):
		t.Fatalf("timed out waiting for %s", what)
	}
	return nil
}

func recvSignal(t *testing.T, ch <-chan struct{}, what string) {
	t.Helper()
	select {
	case <-ch:
	case <-time.After(5 * time.Second):
		t.Fatalf("timed out waiting for %s", what)
	}
}

func expectEvent(t *testing.T, ch <-chan watch.Event, etype, name, uid, rv string) watch.Event {
	t.Helper()
	select {
	case e := <-ch:
		if e.Type != etype || e.Job.Name != name || e.Job.UID != uid || e.Job.ResourceVersion != rv {
			t.Fatalf("event = %s %s uid=%s rv=%s, want %s %s uid=%s rv=%s",
				e.Type, e.Job.Name, e.Job.UID, e.Job.ResourceVersion, etype, name, uid, rv)
		}
		return e
	case <-time.After(5 * time.Second):
		t.Fatalf("timed out waiting for event %s %s", etype, name)
	}
	return watch.Event{}
}

func noPendingEvents(t *testing.T, ch <-chan watch.Event, when string) {
	t.Helper()
	select {
	case e := <-ch:
		t.Fatalf("unexpected extra event %s %s (%s)", e.Type, e.Job.Name, when)
	default:
	}
}

func startWatcher(t *testing.T, f *fakeAPI) (context.CancelFunc, chan watch.Event, chan error) {
	c := newClient(f)
	w := watch.NewWatcher(c, "batch-jobs")
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	events := make(chan watch.Event, 64)
	done := make(chan error, 1)
	go func() { done <- w.Run(ctx, func(e watch.Event) { events <- e }) }()
	return cancel, events, done
}

func checkCommonHeaders(t *testing.T, f *fakeAPI) {
	t.Helper()
	for i, r := range f.recorded() {
		if r.method != "GET" {
			t.Errorf("request %d: method = %s, want GET", i, r.method)
		}
		if r.auth != "Bearer "+token {
			t.Errorf("request %d: Authorization = %q, want bearer token", i, r.auth)
		}
		if r.accept != "application/json" {
			t.Errorf("request %d: Accept = %q, want application/json", i, r.accept)
		}
	}
}

// ---------------------------------------------------------------- tests

func TestListJobs(t *testing.T) {
	f := newFake(t)
	f.lists = []listScript{{200, listJSON("2041",
		jobJSON("etl-hourly", "uid-etl", "1990", 1, 0, 0),
		jobJSON("report-nightly", "uid-rep", "2007", 0, 1, 0,
			cond("Complete", "True", "CompletionsReached")),
	)}}
	jl, err := newClient(f).ListJobs(context.Background(), "batch-jobs")
	if err != nil {
		t.Fatalf("ListJobs: %v", err)
	}
	if jl.ResourceVersion != "2041" {
		t.Errorf("list resourceVersion = %q, want 2041 (from JobList metadata)", jl.ResourceVersion)
	}
	if len(jl.Items) != 2 {
		t.Fatalf("items = %d, want 2", len(jl.Items))
	}
	j0, j1 := jl.Items[0], jl.Items[1]
	if j0.Name != "etl-hourly" || j0.Namespace != "batch-jobs" || j0.UID != "uid-etl" ||
		j0.ResourceVersion != "1990" || j0.Active != 1 {
		t.Errorf("first job decoded wrong: %+v", j0)
	}
	if ct, ok := j0.TerminalCondition(); ok || ct != "" {
		t.Errorf("a running job must not report a terminal condition, got %q", ct)
	}
	if j1.Succeeded != 1 || len(j1.Conditions) != 1 || j1.Conditions[0].Reason != "CompletionsReached" {
		t.Errorf("second job decoded wrong: %+v", j1)
	}
	if ct, ok := j1.TerminalCondition(); !ok || ct != "Complete" {
		t.Errorf("TerminalCondition = %q,%v, want Complete,true", ct, ok)
	}
	reqs := f.recorded()
	if reqs[0].path != "/apis/batch/v1/namespaces/batch-jobs/jobs" {
		t.Errorf("list path = %q", reqs[0].path)
	}
	if reqs[0].query.Has("watch") || reqs[0].query.Has("resourceVersion") {
		t.Errorf("a plain list must not send watch/resourceVersion, got query %q", reqs[0].query.Encode())
	}
	checkCommonHeaders(t, f)
}

func TestListStatusError(t *testing.T) {
	f := newFake(t)
	f.lists = []listScript{{403, statusJSON(403, "Forbidden",
		`jobs.batch is forbidden: User "system:serviceaccount:ci:watcher" cannot list resource "jobs"`)}}
	_, err := newClient(f).ListJobs(context.Background(), "batch-jobs")
	if err == nil {
		t.Fatal("a 403 Status response must surface as an error")
	}
	var apiErr *watch.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("error type = %T, want *jobwatch.APIError", err)
	}
	if apiErr.Code != 403 || apiErr.Reason != "Forbidden" {
		t.Errorf("APIError = code %d reason %q, want 403 Forbidden", apiErr.Code, apiErr.Reason)
	}
	if !strings.Contains(apiErr.Message, "cannot list") {
		t.Errorf("APIError.Message = %q, want the Status message", apiErr.Message)
	}
	if !strings.Contains(err.Error(), "Forbidden") {
		t.Errorf("Error() = %q, should mention the reason", err.Error())
	}
	if strings.Contains(err.Error(), token) {
		t.Error("bearer token leaked into error text")
	}
}

func TestWatcherStreamsEvents(t *testing.T) {
	f := newFake(t)
	f.lists = []listScript{{200, listJSON("100",
		jobJSON("etl-hourly", "uid-etl", "90", 1, 0, 0),
		jobJSON("img-resize", "uid-img", "95", 1, 0, 0))}}
	f.watches = []watchScript{
		{frames: []string{
			frame("MODIFIED", jobJSON("etl-hourly", "uid-etl", "101", 0, 1, 0,
				cond("Complete", "True", "CompletionsReached"))),
			frame("ADDED", jobJSON("backfill", "uid-back", "102", 1, 0, 0)),
			frame("DELETED", jobJSON("img-resize", "uid-img", "103", 0, 0, 0)),
		}, splitFirst: true},
		{hold: true},
	}
	cancel, events, done := startWatcher(t, f)

	recvValues(t, f.listStarted, "initial list")
	expectEvent(t, events, "ADDED", "etl-hourly", "uid-etl", "90")
	expectEvent(t, events, "ADDED", "img-resize", "uid-img", "95")

	q1 := recvValues(t, f.watchStarted, "first watch request")
	if v := q1.Get("watch"); v != "true" && v != "1" {
		t.Errorf("watch param = %q, want true or 1", v)
	}
	if q1.Get("allowWatchBookmarks") != "true" {
		t.Errorf("allowWatchBookmarks = %q, want true", q1.Get("allowWatchBookmarks"))
	}
	if q1.Get("resourceVersion") != "100" {
		t.Errorf("watch resourceVersion = %q, want the list's 100", q1.Get("resourceVersion"))
	}

	e := expectEvent(t, events, "MODIFIED", "etl-hourly", "uid-etl", "101")
	if e.Job.Succeeded != 1 {
		t.Errorf("modified job succeeded = %d, want 1", e.Job.Succeeded)
	}
	if ct, ok := e.Job.TerminalCondition(); !ok || ct != "Complete" {
		t.Errorf("TerminalCondition = %q,%v, want Complete,true", ct, ok)
	}
	expectEvent(t, events, "ADDED", "backfill", "uid-back", "102")
	expectEvent(t, events, "DELETED", "img-resize", "uid-img", "103")

	q2 := recvValues(t, f.watchStarted, "re-watch after clean EOF")
	if q2.Get("resourceVersion") != "103" {
		t.Errorf("re-watch resourceVersion = %q, want 103 (last event's object version)",
			q2.Get("resourceVersion"))
	}
	noPendingEvents(t, events, "after stream drained")

	cancel()
	if err := recvErr(t, done, "Run return"); !errors.Is(err, context.Canceled) {
		t.Errorf("Run returned %v, want context.Canceled", err)
	}
	recvSignal(t, f.disconnected, "server-side disconnect after cancel")
	checkCommonHeaders(t, f)
}

func TestBookmarkAdvancesResumeVersion(t *testing.T) {
	f := newFake(t)
	f.lists = []listScript{{200, listJSON("200",
		jobJSON("etl-hourly", "uid-etl", "150", 1, 0, 0))}}
	f.watches = []watchScript{
		{frames: []string{bookmarkFrame("260")}},
		{hold: true},
	}
	cancel, events, done := startWatcher(t, f)

	expectEvent(t, events, "ADDED", "etl-hourly", "uid-etl", "150")
	q1 := recvValues(t, f.watchStarted, "first watch request")
	if q1.Get("resourceVersion") != "200" {
		t.Errorf("first watch resourceVersion = %q, want 200", q1.Get("resourceVersion"))
	}
	q2 := recvValues(t, f.watchStarted, "watch after bookmark")
	if q2.Get("resourceVersion") != "260" {
		t.Errorf("post-bookmark resourceVersion = %q, want 260 (bookmark must advance it)",
			q2.Get("resourceVersion"))
	}
	noPendingEvents(t, events, "a BOOKMARK must not be delivered to the sink")

	cancel()
	if err := recvErr(t, done, "Run return"); !errors.Is(err, context.Canceled) {
		t.Errorf("Run returned %v, want context.Canceled", err)
	}
}

func relistFixture(f *fakeAPI) {
	f.lists = []listScript{
		{200, listJSON("300",
			jobJSON("etl-hourly", "uid-etl", "290", 1, 0, 0),
			jobJSON("img-resize", "uid-img", "295", 1, 0, 0))},
		{200, listJSON("400",
			jobJSON("etl-hourly", "uid-etl", "390", 0, 1, 0,
				cond("Complete", "True", "CompletionsReached")),
			jobJSON("backfill", "uid-back", "395", 1, 0, 0))},
	}
}

func expectRelistRecovery(t *testing.T, f *fakeAPI, cancel context.CancelFunc,
	events chan watch.Event, done chan error) {
	t.Helper()
	recvValues(t, f.listStarted, "initial list")
	expectEvent(t, events, "ADDED", "etl-hourly", "uid-etl", "290")
	expectEvent(t, events, "ADDED", "img-resize", "uid-img", "295")

	q1 := recvValues(t, f.watchStarted, "expired watch request")
	if q1.Get("resourceVersion") != "300" {
		t.Errorf("expired watch resourceVersion = %q, want 300", q1.Get("resourceVersion"))
	}

	relistQ := recvValues(t, f.listStarted, "recovery list")
	if relistQ.Has("resourceVersion") {
		t.Errorf("recovery list must not pin a resourceVersion, got query %q", relistQ.Encode())
	}
	expectEvent(t, events, "MODIFIED", "etl-hourly", "uid-etl", "390")
	expectEvent(t, events, "ADDED", "backfill", "uid-back", "395")
	del := expectEvent(t, events, "DELETED", "img-resize", "uid-img", "295")
	if del.Job.Active != 1 {
		t.Errorf("DELETED must carry the last known job state, got %+v", del.Job)
	}

	q2 := recvValues(t, f.watchStarted, "watch after recovery")
	if q2.Get("resourceVersion") != "400" {
		t.Errorf("post-recovery watch resourceVersion = %q, want 400", q2.Get("resourceVersion"))
	}
	noPendingEvents(t, events, "after relist diff")

	cancel()
	if err := recvErr(t, done, "Run return"); !errors.Is(err, context.Canceled) {
		t.Errorf("Run returned %v, want context.Canceled", err)
	}
}

func TestExpiredHTTP410Relists(t *testing.T) {
	f := newFake(t)
	relistFixture(f)
	f.watches = []watchScript{
		{status: 410, statusBody: statusJSON(410, "Expired", "too old resource version: 300 (401)")},
		{hold: true},
	}
	cancel, events, done := startWatcher(t, f)
	expectRelistRecovery(t, f, cancel, events, done)
}

func TestExpiredErrorFrameRelists(t *testing.T) {
	f := newFake(t)
	relistFixture(f)
	f.watches = []watchScript{
		{frames: []string{frame("ERROR", statusJSON(410, "Expired", "too old resource version: 300 (401)"))}},
		{hold: true},
	}
	cancel, events, done := startWatcher(t, f)
	expectRelistRecovery(t, f, cancel, events, done)
}

func TestErrorFrameNon410IsFatal(t *testing.T) {
	f := newFake(t)
	f.lists = []listScript{{200, listJSON("500",
		jobJSON("etl-hourly", "uid-etl", "480", 1, 0, 0))}}
	f.watches = []watchScript{
		{frames: []string{frame("ERROR", statusJSON(500, "InternalError", "etcd leader changed"))}},
	}
	_, events, done := startWatcher(t, f)
	expectEvent(t, events, "ADDED", "etl-hourly", "uid-etl", "480")
	err := recvErr(t, done, "Run return")
	var apiErr *watch.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("Run returned %T (%v), want *jobwatch.APIError", err, err)
	}
	if apiErr.Code != 500 || apiErr.Reason != "InternalError" {
		t.Errorf("APIError = code %d reason %q, want 500 InternalError", apiErr.Code, apiErr.Reason)
	}
}

func TestContextCancellationDuringOpenWatch(t *testing.T) {
	f := newFake(t)
	f.lists = []listScript{{200, listJSON("600")}}
	f.watches = []watchScript{{hold: true}}
	cancel, events, done := startWatcher(t, f)

	recvValues(t, f.watchStarted, "watch request")
	noPendingEvents(t, events, "empty list emits nothing")
	cancel()
	if err := recvErr(t, done, "Run return"); !errors.Is(err, context.Canceled) {
		t.Errorf("Run returned %v, want context.Canceled", err)
	}
	recvSignal(t, f.disconnected, "server-side disconnect after cancel")
}

func TestWaitForJobComplete(t *testing.T) {
	f := newFake(t)
	f.lists = []listScript{{200, listJSON("700",
		jobJSON("nightly-report", "uid-nr", "690", 1, 0, 0),
		jobJSON("other-export", "uid-oe", "695", 1, 0, 0))}}
	f.watches = []watchScript{{frames: []string{
		frame("MODIFIED", jobJSON("other-export", "uid-oe", "700", 0, 1, 0,
			cond("Complete", "True", "CompletionsReached"))),
		frame("MODIFIED", jobJSON("nightly-report", "uid-nr", "701", 1, 0, 0,
			cond("SuccessCriteriaMet", "True", "SuccessPolicy"))),
		frame("MODIFIED", jobJSON("nightly-report", "uid-nr", "702", 0, 1, 0,
			cond("SuccessCriteriaMet", "True", "SuccessPolicy"),
			cond("Complete", "True", "CompletionsReached"))),
	}, hold: true}}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	job, err := watch.WaitForJob(ctx, newClient(f), "batch-jobs", "nightly-report")
	if err != nil {
		t.Fatalf("WaitForJob: %v", err)
	}
	if job.ResourceVersion != "702" {
		t.Errorf("returned job rv = %q, want 702 — SuccessCriteriaMet alone (rv 701) and a "+
			"different job's Complete (rv 700) must not satisfy the wait", job.ResourceVersion)
	}
	if job.Succeeded != 1 {
		t.Errorf("job.Succeeded = %d, want 1", job.Succeeded)
	}
	if ct, ok := job.TerminalCondition(); !ok || ct != "Complete" {
		t.Errorf("TerminalCondition = %q,%v, want Complete,true", ct, ok)
	}
	recvSignal(t, f.disconnected, "WaitForJob must close its watch stream")
}

func TestWaitForJobFailed(t *testing.T) {
	f := newFake(t)
	f.lists = []listScript{{200, listJSON("800",
		jobJSON("flaky-import", "uid-fi", "790", 1, 0, 1))}}
	f.watches = []watchScript{{frames: []string{
		frame("MODIFIED", jobJSON("flaky-import", "uid-fi", "801", 0, 0, 1,
			cond("Suspended", "True", "JobSuspended"))),
		frame("MODIFIED", jobJSON("flaky-import", "uid-fi", "802", 0, 0, 2,
			cond("Failed", "True", "BackoffLimitExceeded"))),
	}, hold: true}}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	job, err := watch.WaitForJob(ctx, newClient(f), "batch-jobs", "flaky-import")
	if err != nil {
		t.Fatalf("WaitForJob must return a failed job as a result, not an error: %v", err)
	}
	if job.ResourceVersion != "802" {
		t.Errorf("returned job rv = %q, want 802 — Suspended=True (rv 801) is not terminal",
			job.ResourceVersion)
	}
	if ct, ok := job.TerminalCondition(); !ok || ct != "Failed" {
		t.Errorf("TerminalCondition = %q,%v, want Failed,true", ct, ok)
	}
	if job.Failed != 2 {
		t.Errorf("job.Failed = %d, want 2", job.Failed)
	}
}

func TestWaitForJobAlreadyTerminal(t *testing.T) {
	f := newFake(t)
	f.lists = []listScript{{200, listJSON("900",
		jobJSON("done-job", "uid-dj", "890", 0, 1, 0,
			cond("Complete", "True", "CompletionsReached")))}}
	f.watches = []watchScript{{hold: true}}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	job, err := watch.WaitForJob(ctx, newClient(f), "batch-jobs", "done-job")
	if err != nil {
		t.Fatalf("WaitForJob: %v", err)
	}
	if job.ResourceVersion != "890" {
		t.Errorf("a job already terminal at list time must resolve immediately, got rv %q",
			job.ResourceVersion)
	}
}

// ---------------------------------------------------------------- fixtures

func loadJSON(t *testing.T, path string) map[string]any {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("protected fixture missing: %v", err)
	}
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatalf("%s does not parse: %v", path, err)
	}
	return m
}

func TestProtectedDocsFixtures(t *testing.T) {
	sources := loadJSON(t, "docs/official_sources.json")
	research, ok := sources["research"].(map[string]any)
	if !ok {
		t.Fatal("official_sources.json must embed the research object")
	}
	if research["required"] != true {
		t.Error("research.required must be true")
	}
	srcs, _ := research["official_sources"].([]any)
	if len(srcs) < 2 {
		t.Fatalf("need at least two official sources, got %d", len(srcs))
	}
	for i, s := range srcs {
		src, _ := s.(map[string]any)
		u, _ := src["url"].(string)
		firstParty := strings.Contains(u, "kubernetes.io") ||
			strings.Contains(u, "github.com/kubernetes/kubernetes") ||
			strings.Contains(u, "githubusercontent.com/kubernetes/kubernetes")
		if !strings.HasPrefix(u, "https://") || !firstParty {
			t.Errorf("source %d is not a first-party Kubernetes URL: %q", i, u)
		}
		if uf, _ := src["used_for"].(string); uf == "" {
			t.Errorf("source %d is missing used_for", i)
		}
	}
	if facts, _ := sources["verified_facts"].([]any); len(facts) < 4 {
		t.Errorf("verified_facts must summarize at least 4 contract facts, got %d", len(facts))
	}

	contract := loadJSON(t, "docs/contract.json")
	list, _ := contract["list"].(map[string]any)
	if list["path"] != "/apis/batch/v1/namespaces/{namespace}/jobs" {
		t.Errorf("contract list path = %v", list["path"])
	}
	w, _ := contract["watch"].(map[string]any)
	params, _ := w["params"].(map[string]any)
	if params["allowWatchBookmarks"] != "true" || params["watch"] != "true" {
		t.Errorf("contract watch params = %v", params)
	}
	expired, _ := contract["expired"].(map[string]any)
	if code, _ := expired["code"].(float64); int(code) != 410 || expired["reason"] != "Expired" {
		t.Errorf("contract expired = %v", expired)
	}
	terms, _ := contract["terminal_conditions"].([]any)
	joined := fmt.Sprint(terms)
	if !strings.Contains(joined, "Complete") || !strings.Contains(joined, "Failed") {
		t.Errorf("contract terminal_conditions = %v", terms)
	}
}
