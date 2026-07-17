// Acceptance tests for the ddmon package: a Datadog monitor inventory and
// reconciler over the documented v1 monitor operations. Runs a loopback fake
// Datadog API; no real Datadog, no real credentials, no wall-clock sleeps —
// waiting is injected and recorded. The wire contract the fake enforces is
// pinned in docs/contract.json. This file and everything under docs/ are
// protected.
package ddmon

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"reflect"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

const (
	apiKeyFixture = "ddfixtureapi41d7e2b8a3f605dummy0"
	appKeyFixture = "ddfixtureapp9c41d7e2b8a3f605dummy00abcd"
)

type recordedReq struct {
	method string
	path   string
	query  url.Values
	header http.Header
	body   []byte
}

type putScript struct {
	status  int
	body    string
	headers map[string]string
}

type fakeDD struct {
	mu          sync.Mutex
	reqs        []recordedReq
	monitors    []map[string]any
	pageHeaders map[int]map[string]string
	putScripts  map[int64][]putScript
}

func (f *fakeDD) record(r *http.Request) recordedReq {
	body, _ := io.ReadAll(r.Body)
	rec := recordedReq{
		method: r.Method,
		path:   r.URL.Path,
		query:  r.URL.Query(),
		header: r.Header.Clone(),
		body:   body,
	}
	f.mu.Lock()
	f.reqs = append(f.reqs, rec)
	f.mu.Unlock()
	return rec
}

func (f *fakeDD) requests() []recordedReq {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]recordedReq(nil), f.reqs...)
}

func (f *fakeDD) handler() http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		rec := f.record(r)
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/api/v1/monitor":
			page, _ := strconv.Atoi(rec.query.Get("page"))
			size, err := strconv.Atoi(rec.query.Get("page_size"))
			if err != nil || size <= 0 {
				w.WriteHeader(400)
				fmt.Fprint(w, `{"errors":["page_size missing"]}`)
				return
			}
			lo := page * size
			hi := lo + size
			if lo > len(f.monitors) {
				lo = len(f.monitors)
			}
			if hi > len(f.monitors) {
				hi = len(f.monitors)
			}
			for k, v := range f.pageHeaders[page] {
				w.Header().Set(k, v)
			}
			w.WriteHeader(200)
			out, _ := json.Marshal(f.monitors[lo:hi])
			w.Write(out)
		case r.Method == http.MethodPut && len(r.URL.Path) > len("/api/v1/monitor/") &&
			r.URL.Path[:len("/api/v1/monitor/")] == "/api/v1/monitor/":
			id, _ := strconv.ParseInt(r.URL.Path[len("/api/v1/monitor/"):], 10, 64)
			f.mu.Lock()
			scripts := f.putScripts[id]
			var script putScript
			if len(scripts) > 0 {
				script, f.putScripts[id] = scripts[0], scripts[1:]
			} else {
				script = putScript{status: 200, body: fmt.Sprintf(`{"id":%d}`, id)}
			}
			f.mu.Unlock()
			for k, v := range script.headers {
				w.Header().Set(k, v)
			}
			w.WriteHeader(script.status)
			fmt.Fprint(w, script.body)
		default:
			w.WriteHeader(404)
			fmt.Fprint(w, `{"errors":["unexpected path: only documented v1 monitor operations exist"]}`)
		}
	}
}

func monitorFixture(id int64, name, query, message string, tags []string,
	priority any, restrictedRoles any, options map[string]any) map[string]any {
	m := map[string]any{
		"id":      id,
		"name":    name,
		"type":    "query alert",
		"query":   query,
		"message": message,
		"tags":    tags,
		"options": options,
		// readonly server-side fields a client must tolerate and never echo
		"created":       "2025-11-02T09:00:00.000Z",
		"modified":      "2026-06-30T10:15:00.000Z",
		"creator":       map[string]any{"email": "sre@example.test", "name": "SRE Bot"},
		"overall_state": "OK",
		"multi":         false,
	}
	if priority != nil {
		m["priority"] = priority
	} else {
		m["priority"] = nil
	}
	if restrictedRoles != nil {
		m["restricted_roles"] = restrictedRoles
	}
	return m
}

func inventory() []map[string]any {
	return []map[string]any{
		monitorFixture(101, "checkout error rate",
			"avg(last_10m):avg:checkout.errors{env:prod} > 100",
			"Checkout errors above threshold @slack-checkout",
			[]string{"service:checkout", "managed-by:ddmon"},
			3,
			[]string{"3f7c8a2e-52b7-11ec-aaa2-da7ad0900002", "9a1b46c0-52b7-11ec-8b46-da7ad0900002"},
			map[string]any{
				"thresholds":             map[string]any{"critical": 95},
				"notify_no_data":         true,
				"renotify_interval":      40,
				"escalation_message":     "still failing",
				"undocumented_beta_flag": "keep-me",
			}),
		monitorFixture(102, "db replica lag",
			"avg(last_5m):avg:postgres.replication.lag{env:prod} > 30",
			"Replica lag high @pagerduty-db",
			[]string{"service:db", "managed-by:ddmon"},
			4,
			nil,
			map[string]any{"thresholds": map[string]any{"critical": 30}}),
		monitorFixture(103, "cache hit ratio",
			"avg(last_15m):avg:redis.cache.hit_ratio{env:prod} < 0.5",
			"Cache hit ratio dropped",
			[]string{"service:cache", "managed-by:ddmon"},
			nil,
			nil,
			map[string]any{
				"thresholds":   map[string]any{"critical": 0.5},
				"notify_audit": false,
			}),
		monitorFixture(104, "disk space forecast",
			"forecast(avg:system.disk.in_use{*}, '1w') >= 0.9",
			"Disk filling up",
			[]string{"team:infra"},
			2,
			[]string{"77aa11bb-52b7-11ec-9f00-da7ad0900002"},
			map[string]any{"locked": false}),
		monitorFixture(105, "ingest queue depth",
			"max(last_5m):max:ingest.queue.depth{env:prod} > 50000",
			"Ingest queue backing up @slack-ingest",
			[]string{"service:ingest", "managed-by:ddmon"},
			3,
			[]string{"c4d9e1f2-52b7-11ec-9f00-da7ad0900002"},
			map[string]any{"locked": false, "renotify_interval": 0}),
	}
}

func intp(v int64) *int64 { return &v }

func newTestClient(t *testing.T, base string, pageSize, maxRetries int) (*Client, *[]time.Duration) {
	t.Helper()
	var mu sync.Mutex
	sleeps := &[]time.Duration{}
	c := NewClient(Config{
		BaseURL:    base,
		APIKey:     apiKeyFixture,
		AppKey:     appKeyFixture,
		PageSize:   pageSize,
		MaxRetries: maxRetries,
		Sleep: func(d time.Duration) {
			mu.Lock()
			*sleeps = append(*sleeps, d)
			mu.Unlock()
		},
	})
	return c, sleeps
}

func decodeBody(t *testing.T, b []byte) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatalf("update body is not JSON: %v (%q)", err, b)
	}
	return m
}

func TestProtectedDocsFixtures(t *testing.T) {
	raw, err := os.ReadFile("docs/official_sources.json")
	if err != nil {
		t.Fatalf("missing protected provenance: %v", err)
	}
	var doc struct {
		Research struct {
			Required        bool `json:"required"`
			OfficialSources []struct {
				URL string `json:"url"`
			} `json:"official_sources"`
		} `json:"research"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatalf("official_sources.json does not parse: %v", err)
	}
	if !doc.Research.Required || len(doc.Research.OfficialSources) < 2 {
		t.Fatalf("research provenance incomplete")
	}
	if _, err := os.ReadFile("docs/contract.json"); err != nil {
		t.Fatalf("missing protected contract: %v", err)
	}
}

func TestListPaginationAndWireContract(t *testing.T) {
	fake := &fakeDD{monitors: inventory(), pageHeaders: map[int]map[string]string{}}
	srv := httptest.NewServer(fake.handler())
	defer srv.Close()

	client, _ := newTestClient(t, srv.URL, 2, 2)
	monitors, err := client.ListMonitors(context.Background(),
		Filter{MonitorTags: []string{"managed-by:ddmon", "env:prod"}})
	if err != nil {
		t.Fatalf("ListMonitors: %v", err)
	}
	reqs := fake.requests()
	if len(reqs) != 3 {
		t.Fatalf("want 3 page requests (2+2+1 with page_size=2), got %d", len(reqs))
	}
	for i, r := range reqs {
		if r.method != http.MethodGet || r.path != "/api/v1/monitor" {
			t.Fatalf("req %d: want GET /api/v1/monitor, got %s %s", i, r.method, r.path)
		}
		if got := r.query.Get("page"); got != strconv.Itoa(i) {
			t.Fatalf("req %d: page=%q, want %d", i, got, i)
		}
		if got := r.query.Get("page_size"); got != "2" {
			t.Fatalf("req %d: page_size=%q, want 2", i, got)
		}
		if got := r.query.Get("monitor_tags"); got != "managed-by:ddmon,env:prod" {
			t.Fatalf("req %d: monitor_tags=%q", i, got)
		}
		if r.header.Get("DD-API-KEY") != apiKeyFixture {
			t.Fatalf("req %d: DD-API-KEY header missing or wrong", i)
		}
		if r.header.Get("DD-APPLICATION-KEY") != appKeyFixture {
			t.Fatalf("req %d: DD-APPLICATION-KEY header missing or wrong", i)
		}
		if r.query.Get("api_key") != "" || r.query.Get("application_key") != "" {
			t.Fatalf("req %d: credentials must never travel in the query string", i)
		}
	}
	if len(monitors) != 5 {
		t.Fatalf("want 5 monitors, got %d", len(monitors))
	}
	m := monitors[0]
	if m.ID != 101 || m.Name != "checkout error rate" {
		t.Fatalf("first monitor mismatch: %+v", m)
	}
	if m.Query == "" || m.Message == "" || m.Type != "query alert" {
		t.Fatalf("monitor fields not decoded: %+v", m)
	}
	if m.Priority == nil || *m.Priority != 3 {
		t.Fatalf("priority not decoded: %+v", m.Priority)
	}
	if len(m.RestrictedRoles) != 2 {
		t.Fatalf("restricted_roles not decoded: %+v", m.RestrictedRoles)
	}
	var opts map[string]any
	if err := json.Unmarshal(m.Options, &opts); err != nil {
		t.Fatalf("options must be retained as raw JSON: %v", err)
	}
	if opts["undocumented_beta_flag"] != "keep-me" {
		t.Fatalf("unknown option keys must survive decoding: %v", opts)
	}
	if monitors[2].Priority != nil {
		t.Fatalf("null priority must decode as unset, got %v", *monitors[2].Priority)
	}
}

func TestListDefaultPageSizeIs100(t *testing.T) {
	fake := &fakeDD{monitors: inventory(), pageHeaders: map[int]map[string]string{}}
	srv := httptest.NewServer(fake.handler())
	defer srv.Close()

	client, _ := newTestClient(t, srv.URL, 0, 2)
	if _, err := client.ListMonitors(context.Background(), Filter{}); err != nil {
		t.Fatalf("ListMonitors: %v", err)
	}
	reqs := fake.requests()
	if len(reqs) != 1 {
		t.Fatalf("5 monitors fit one default page; got %d requests", len(reqs))
	}
	if got := reqs[0].query.Get("page_size"); got != "100" {
		t.Fatalf("default page_size must match the official pager (100), got %q", got)
	}
	if reqs[0].query.Has("monitor_tags") || reqs[0].query.Has("name") {
		t.Fatalf("empty filter must not emit filter params: %v", reqs[0].query)
	}
}

func TestRateLimitHeadersThrottleNextRequest(t *testing.T) {
	fake := &fakeDD{monitors: inventory(), pageHeaders: map[int]map[string]string{
		0: {
			"X-RateLimit-Limit":     "100",
			"X-RateLimit-Period":    "10",
			"X-RateLimit-Remaining": "0",
			"X-RateLimit-Reset":     "2",
			"X-RateLimit-Name":      "monitors",
		},
	}}
	srv := httptest.NewServer(fake.handler())
	defer srv.Close()

	client, sleeps := newTestClient(t, srv.URL, 2, 2)
	if _, err := client.ListMonitors(context.Background(), Filter{}); err != nil {
		t.Fatalf("ListMonitors: %v", err)
	}
	if len(fake.requests()) != 3 {
		t.Fatalf("want 3 page requests, got %d", len(fake.requests()))
	}
	if !reflect.DeepEqual(*sleeps, []time.Duration{2 * time.Second}) {
		t.Fatalf("exhausted X-RateLimit-Remaining must wait X-RateLimit-Reset seconds before the next request; sleeps=%v", *sleeps)
	}
}

func desiredSet() []Desired {
	return []Desired{
		{
			Name:     "checkout error rate",
			Query:    "avg(last_10m):avg:checkout.errors{env:prod} > 120",
			Message:  "Checkout errors above threshold @slack-checkout @pagerduty-checkout",
			Tags:     []string{"service:checkout", "managed-by:ddmon", "team:payments"},
			Priority: intp(2),
		},
		{
			Name:     "db replica lag",
			Query:    "avg(last_5m):avg:postgres.replication.lag{env:prod} > 30",
			Message:  "Replica lag high @pagerduty-db",
			Tags:     []string{"managed-by:ddmon", "service:db"}, // same set, shuffled
			Priority: intp(4),
		},
		{
			Name:    "cache hit ratio",
			Query:   "avg(last_15m):avg:redis.cache.hit_ratio{env:prod} < 0.4",
			Message: "Cache hit ratio dropped",
			Tags:    []string{"service:cache", "managed-by:ddmon"},
		},
		{
			Name:     "ingest queue depth",
			Query:    "max(last_5m):max:ingest.queue.depth{env:prod} > 50000",
			Message:  "Ingest queue backing up @slack-ingest",
			Tags:     []string{"service:ingest", "managed-by:ddmon"},
			Priority: intp(1),
		},
		{
			Name:     "payment queue depth",
			Query:    "max(last_5m):max:payment.queue.depth{env:prod} > 1000",
			Message:  "Payment queue backing up",
			Tags:     []string{"service:payments", "managed-by:ddmon"},
			Priority: intp(2),
		},
	}
}

func TestReconcileUpdatesOnlyOwnedFields(t *testing.T) {
	fake := &fakeDD{monitors: inventory(), pageHeaders: map[int]map[string]string{}}
	srv := httptest.NewServer(fake.handler())
	defer srv.Close()

	client, _ := newTestClient(t, srv.URL, 100, 2)
	report, err := client.Reconcile(context.Background(), Filter{}, desiredSet())
	if err != nil {
		t.Fatalf("Reconcile: %v", err)
	}

	var puts []recordedReq
	for _, r := range fake.requests() {
		if r.method == http.MethodPut {
			puts = append(puts, r)
		}
		if r.method == http.MethodPut && r.header.Get("Content-Type") != "application/json" {
			t.Fatalf("PUT must send Content-Type: application/json")
		}
	}
	if len(puts) != 3 {
		t.Fatalf("want PUTs for 101, 103, 105 only; got %d", len(puts))
	}
	wantPaths := []string{"/api/v1/monitor/101", "/api/v1/monitor/103", "/api/v1/monitor/105"}
	for i, p := range puts {
		if p.path != wantPaths[i] {
			t.Fatalf("PUT %d path %q, want %q (desired order)", i, p.path, wantPaths[i])
		}
	}

	// 101: owned fields updated; options and restricted_roles echoed verbatim
	body := decodeBody(t, puts[0].body)
	if body["query"] != "avg(last_10m):avg:checkout.errors{env:prod} > 120" {
		t.Fatalf("101 query not updated: %v", body["query"])
	}
	if body["message"] != "Checkout errors above threshold @slack-checkout @pagerduty-checkout" {
		t.Fatalf("101 message not updated: %v", body["message"])
	}
	if body["priority"] != float64(2) {
		t.Fatalf("101 priority not updated: %v", body["priority"])
	}
	wantTags := []any{"service:checkout", "managed-by:ddmon", "team:payments"}
	if !reflect.DeepEqual(body["tags"], wantTags) {
		t.Fatalf("101 tags: %v", body["tags"])
	}
	wantOptions := map[string]any{
		"thresholds":             map[string]any{"critical": float64(95)},
		"notify_no_data":         true,
		"renotify_interval":      float64(40),
		"escalation_message":     "still failing",
		"undocumented_beta_flag": "keep-me",
	}
	if !reflect.DeepEqual(body["options"], wantOptions) {
		t.Fatalf("101 options must be echoed verbatim (unknown keys preserved): %v", body["options"])
	}
	wantRoles := []any{"3f7c8a2e-52b7-11ec-aaa2-da7ad0900002", "9a1b46c0-52b7-11ec-8b46-da7ad0900002"}
	if !reflect.DeepEqual(body["restricted_roles"], wantRoles) {
		t.Fatalf("101 restricted_roles must be echoed verbatim: %v", body["restricted_roles"])
	}
	for _, forbidden := range []string{"id", "name", "type", "created", "creator",
		"modified", "overall_state", "matching_downtimes", "multi", "state", "deleted"} {
		if _, present := body[forbidden]; present {
			t.Fatalf("update body must not send %q", forbidden)
		}
	}

	// 103: unrestricted monitor — restricted_roles key must be omitted, never null
	body = decodeBody(t, puts[1].body)
	if _, present := body["restricted_roles"]; present {
		t.Fatalf("unrestricted monitor: restricted_roles must be omitted entirely (null would open editing to everyone), got %v", body["restricted_roles"])
	}
	if _, present := body["priority"]; present {
		t.Fatalf("desired without priority must not send priority, got %v", body["priority"])
	}
	if body["query"] != "avg(last_15m):avg:redis.cache.hit_ratio{env:prod} < 0.4" {
		t.Fatalf("103 query not updated: %v", body["query"])
	}
	if !reflect.DeepEqual(body["options"], map[string]any{
		"thresholds":   map[string]any{"critical": 0.5},
		"notify_audit": false,
	}) {
		t.Fatalf("103 options must be echoed verbatim: %v", body["options"])
	}

	// 105: priority-only drift still carries echoed unowned fields
	body = decodeBody(t, puts[2].body)
	if body["priority"] != float64(1) {
		t.Fatalf("105 priority: %v", body["priority"])
	}
	if !reflect.DeepEqual(body["restricted_roles"], []any{"c4d9e1f2-52b7-11ec-9f00-da7ad0900002"}) {
		t.Fatalf("105 restricted_roles: %v", body["restricted_roles"])
	}

	if !reflect.DeepEqual(report.Updated, []int64{101, 103, 105}) {
		t.Fatalf("Updated: %v", report.Updated)
	}
	if !reflect.DeepEqual(report.Unchanged, []int64{102}) {
		t.Fatalf("tag order must not count as drift; Unchanged: %v", report.Unchanged)
	}
	if !reflect.DeepEqual(report.Missing, []string{"payment queue depth"}) {
		t.Fatalf("Missing: %v", report.Missing)
	}
	if len(report.Failed) != 0 {
		t.Fatalf("Failed should be empty: %v", report.Failed)
	}
}

func TestRetryOn429HonorsRetryAfterThenReset(t *testing.T) {
	fake := &fakeDD{monitors: inventory(), pageHeaders: map[int]map[string]string{},
		putScripts: map[int64][]putScript{
			101: {
				{status: 429, body: `{"errors":["rate limited"]}`,
					headers: map[string]string{"Retry-After": "1", "X-RateLimit-Reset": "9"}},
				{status: 200, body: `{"id":101}`},
			},
		}}
	srv := httptest.NewServer(fake.handler())
	defer srv.Close()

	client, sleeps := newTestClient(t, srv.URL, 100, 2)
	report, err := client.Reconcile(context.Background(), Filter{}, desiredSet()[:1])
	if err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	var putCount int
	for _, r := range fake.requests() {
		if r.method == http.MethodPut {
			putCount++
		}
	}
	if putCount != 2 {
		t.Fatalf("429 must be retried once here; got %d PUTs", putCount)
	}
	if !reflect.DeepEqual(*sleeps, []time.Duration{1 * time.Second}) {
		t.Fatalf("Retry-After takes precedence over X-RateLimit-Reset; sleeps=%v", *sleeps)
	}
	if !reflect.DeepEqual(report.Updated, []int64{101}) || len(report.Failed) != 0 {
		t.Fatalf("report after successful retry: %+v", report)
	}

	// exhaustion: no Retry-After, fall back to X-RateLimit-Reset; MaxRetries=1
	fake2 := &fakeDD{monitors: inventory(), pageHeaders: map[int]map[string]string{},
		putScripts: map[int64][]putScript{
			101: {
				{status: 429, body: `{"errors":["rate limited"]}`,
					headers: map[string]string{"X-RateLimit-Reset": "3"}},
				{status: 429, body: `{"errors":["rate limited"]}`,
					headers: map[string]string{"X-RateLimit-Reset": "3"}},
			},
		}}
	srv2 := httptest.NewServer(fake2.handler())
	defer srv2.Close()

	client2, sleeps2 := newTestClient(t, srv2.URL, 100, 1)
	report2, err := client2.Reconcile(context.Background(), Filter{}, desiredSet()[:1])
	if err != nil {
		t.Fatalf("a rate-limited monitor is a per-monitor failure, not a run failure: %v", err)
	}
	var puts2 int
	for _, r := range fake2.requests() {
		if r.method == http.MethodPut {
			puts2++
		}
	}
	if puts2 != 2 {
		t.Fatalf("MaxRetries=1 means 2 attempts, got %d", puts2)
	}
	if !reflect.DeepEqual(*sleeps2, []time.Duration{3 * time.Second}) {
		t.Fatalf("fallback wait must use X-RateLimit-Reset; sleeps=%v", *sleeps2)
	}
	msg, failed := report2.Failed[101]
	if !failed {
		t.Fatalf("exhausted retries must land in Failed: %+v", report2)
	}
	if msg == "" || !contains(msg, "429") {
		t.Fatalf("failure text should surface the 429 status: %q", msg)
	}
}

func TestPartialFailureContinuesAndRedactsKeys(t *testing.T) {
	fake := &fakeDD{monitors: inventory(), pageHeaders: map[int]map[string]string{},
		putScripts: map[int64][]putScript{
			101: {{status: 403, body: `{"errors":["Forbidden: restricted monitor"]}`}},
		}}
	srv := httptest.NewServer(fake.handler())
	defer srv.Close()

	client, _ := newTestClient(t, srv.URL, 100, 2)
	report, err := client.Reconcile(context.Background(), Filter{}, desiredSet())
	if err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	msg, failed := report.Failed[101]
	if !failed {
		t.Fatalf("403 must be recorded per monitor: %+v", report)
	}
	if !contains(msg, "Forbidden: restricted monitor") {
		t.Fatalf("Datadog error strings must be decoded from the errors envelope: %q", msg)
	}
	if contains(msg, apiKeyFixture) || contains(msg, appKeyFixture) {
		t.Fatalf("credentials leaked into error text")
	}
	if !reflect.DeepEqual(report.Updated, []int64{103, 105}) {
		t.Fatalf("a failed update must not stop the rest: %v", report.Updated)
	}
	if !reflect.DeepEqual(report.Unchanged, []int64{102}) {
		t.Fatalf("Unchanged: %v", report.Unchanged)
	}
}

func TestNoWritesWhenEverythingMatches(t *testing.T) {
	fake := &fakeDD{monitors: inventory(), pageHeaders: map[int]map[string]string{}}
	srv := httptest.NewServer(fake.handler())
	defer srv.Close()

	client, _ := newTestClient(t, srv.URL, 100, 2)
	matching := []Desired{desiredSet()[1]} // db replica lag, identical
	report, err := client.Reconcile(context.Background(), Filter{}, matching)
	if err != nil {
		t.Fatalf("Reconcile: %v", err)
	}
	for _, r := range fake.requests() {
		if r.method != http.MethodGet {
			t.Fatalf("no-drift reconcile must be read-only; saw %s %s", r.method, r.path)
		}
	}
	if !reflect.DeepEqual(report.Unchanged, []int64{102}) || len(report.Updated) != 0 {
		t.Fatalf("report: %+v", report)
	}
}

func contains(haystack, needle string) bool {
	return strings.Contains(haystack, needle)
}
