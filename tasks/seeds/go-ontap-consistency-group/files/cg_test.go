// Acceptance tests for the ONTAP application consistency-group reconciler.
// Loopback mock cluster speaking the pinned contract in docs/contract.json.
// No network, dummy credentials, injected pacing. Protected file.
package cgrecon

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
)

const (
	testUser = "svc-cg"
	testPass = "dummy-pass-011"

	cgPath       = "/api/application/consistency-groups"
	instancePath = "/api/application/consistency-groups/cg-uuid-77"

	jobHref1 = "/api/cluster/jobs/job-cg-1?fields=state,message,error"
	jobHref2 = "/api/cluster/jobs/job-cg-2?fields=state,message,error"
	jobHref3 = "/api/cluster/jobs/job-cg-3?fields=state,message,error"
	jobHref4 = "/api/cluster/jobs/job-cg-4?fields=state,message,error"
	jobHref5 = "/api/cluster/jobs/job-cg-5?fields=state,message,error"

	emptyList = `{"records":[],"num_records":0}`
	foundList = `{"records":[{"uuid":"cg-uuid-77","name":"cg-app-01",` +
		`"_links":{"self":{"href":"/api/application/consistency-groups/cg-uuid-77"}}}],"num_records":1}`

	// Full instance documents carry fields this reconciler does not own
	// (space, _tags, replication_relationships) that must survive untouched.
	docInSync = `{"uuid":"cg-uuid-77","name":"cg-app-01",` +
		`"svm":{"uuid":"svm-uuid-1111","name":"svm_prod"},` +
		`"volumes":[{"name":"vol_app_data","uuid":"vol-uuid-01"},{"name":"vol_app_logs","uuid":"vol-uuid-02"}],` +
		`"qos":{"policy":{"name":"gold-tier","uuid":"qos-uuid-1"}},` +
		`"space":{"size":42949672960,"used":1234567},` +
		`"_tags":["env:prod"],` +
		`"replication_relationships":[{"uuid":"rr-0001"}]}`
	docDrifted = `{"uuid":"cg-uuid-77","name":"cg-app-01",` +
		`"svm":{"uuid":"svm-uuid-1111","name":"svm_prod"},` +
		`"volumes":[{"name":"vol_app_data","uuid":"vol-uuid-01"},{"name":"vol_scratch","uuid":"vol-uuid-99"}],` +
		`"qos":{"policy":{"name":"silver-tier","uuid":"qos-uuid-2"}},` +
		`"space":{"size":42949672960,"used":1234567},` +
		`"_tags":["env:prod"],` +
		`"replication_relationships":[{"uuid":"rr-0001"}]}`
	docUpdated = `{"uuid":"cg-uuid-77","name":"cg-app-01",` +
		`"svm":{"uuid":"svm-uuid-1111","name":"svm_prod"},` +
		`"volumes":[{"name":"vol_app_data","uuid":"vol-uuid-01"},{"name":"vol_scratch","uuid":"vol-uuid-99"},{"name":"vol_app_logs","uuid":"vol-uuid-02"}],` +
		`"qos":{"policy":{"name":"gold-tier","uuid":"qos-uuid-1"}},` +
		`"space":{"size":42949672960,"used":1234567},` +
		`"_tags":["env:prod"],` +
		`"replication_relationships":[{"uuid":"rr-0001"}]}`
)

var expectedAuth = "Basic " + base64.StdEncoding.EncodeToString([]byte(testUser+":"+testPass))

func jobAccepted(uuid, href string) string {
	return `{"job":{"uuid":"` + uuid + `","_links":{"self":{"href":"` + href + `"}}}}`
}

func jobState(uuid, state string) string {
	return `{"uuid":"` + uuid + `","state":"` + state + `","message":"` + state + `"}`
}

func jobFailed(uuid, code, message string) string {
	return `{"uuid":"` + uuid + `","state":"failure","message":"failed",` +
		`"error":{"code":"` + code + `","message":"` + message + `"}}`
}

func apiErrorBody(code, message string) string {
	return `{"error":{"code":"` + code + `","message":"` + message + `"}}`
}

type mockResp struct {
	status int
	body   string
}

type mockReq struct {
	method   string
	path     string
	rawQuery string
	raw      string
	auth     string
	body     map[string]any
}

type mockCluster struct {
	mu     sync.Mutex
	routes map[string][]mockResp
	log    []mockReq
}

func newMock(routes map[string][]mockResp) *mockCluster {
	copied := make(map[string][]mockResp, len(routes))
	for key, queue := range routes {
		copied[key] = append([]mockResp(nil), queue...)
	}
	return &mockCluster{routes: copied}
}

func (m *mockCluster) handler(w http.ResponseWriter, r *http.Request) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var body map[string]any
	if data, err := io.ReadAll(r.Body); err == nil && len(data) > 0 {
		_ = json.Unmarshal(data, &body)
	}
	m.log = append(m.log, mockReq{
		method:   r.Method,
		path:     r.URL.Path,
		rawQuery: r.URL.RawQuery,
		raw:      r.URL.RequestURI(),
		auth:     r.Header.Get("Authorization"),
		body:     body,
	})
	key := r.Method + " " + r.URL.Path
	resp := mockResp{599, apiErrorBody("0", "UNEXPECTED "+key)}
	if queue := m.routes[key]; len(queue) > 0 {
		resp = queue[0]
		m.routes[key] = queue[1:]
	}
	w.Header().Set("Content-Type", "application/hal+json")
	w.WriteHeader(resp.status)
	_, _ = io.WriteString(w, resp.body)
}

func (m *mockCluster) requests() []mockReq {
	m.mu.Lock()
	defer m.mu.Unlock()
	return append([]mockReq(nil), m.log...)
}

func setup(t *testing.T, routes map[string][]mockResp) (*mockCluster, *Reconciler, *[]time.Duration) {
	t.Helper()
	mock := newMock(routes)
	server := httptest.NewServer(http.HandlerFunc(mock.handler))
	t.Cleanup(server.Close)
	sleeps := &[]time.Duration{}
	client := NewClient(server.URL, testUser, testPass, server.Client(), func(d time.Duration) {
		*sleeps = append(*sleeps, d)
	})
	return mock, &Reconciler{Client: client, MaxPolls: 10}, sleeps
}

func spec() Spec {
	return Spec{
		SVM:       "svm_prod",
		Name:      "cg-app-01",
		Volumes:   []VolumeSpec{{Name: "vol_app_data"}, {Name: "vol_app_logs"}},
		QoSPolicy: "gold-tier",
	}
}

func ck(t *testing.T, cond bool, label string) {
	t.Helper()
	if !cond {
		t.Errorf("FAIL: %s", label)
	}
}

func query(t *testing.T, req mockReq) url.Values {
	t.Helper()
	values, err := url.ParseQuery(req.rawQuery)
	if err != nil {
		t.Fatalf("bad query %q: %v", req.rawQuery, err)
	}
	return values
}

func expectedCreateBody() map[string]any {
	return map[string]any{
		"name": "cg-app-01",
		"svm":  map[string]any{"name": "svm_prod"},
		"volumes": []any{
			map[string]any{"name": "vol_app_data", "provisioning_options": map[string]any{"action": "add"}},
			map[string]any{"name": "vol_app_logs", "provisioning_options": map[string]any{"action": "add"}},
		},
		"qos": map[string]any{"policy": map[string]any{"name": "gold-tier"}},
	}
}

func TestCreateFlow(t *testing.T) {
	mock, rec, sleeps := setup(t, map[string][]mockResp{
		"GET " + cgPath:  {{200, emptyList}, {200, foundList}},
		"POST " + cgPath: {{202, jobAccepted("job-cg-1", jobHref1)}},
		"GET /api/cluster/jobs/job-cg-1": {
			{200, jobState("job-cg-1", "running")},
			{200, jobState("job-cg-1", "running")},
			{200, jobState("job-cg-1", "success")},
		},
		"GET " + instancePath: {{200, docInSync}},
	})
	report, err := rec.Reconcile(context.Background(), spec())
	if err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	log := mock.requests()
	ck(t, len(log) == 7, "create flow issues exactly 7 requests")
	for i, req := range log {
		ck(t, req.auth == expectedAuth, "request carries basic auth: "+log[i].raw)
	}
	ck(t, log[0].method == "GET" && log[0].path == cgPath, "flow starts with the list lookup")
	q0 := query(t, log[0])
	ck(t, q0.Get("name") == "cg-app-01", "list filters name")
	ck(t, q0.Get("svm.name") == "svm_prod", "list filters svm.name")
	ck(t, q0.Get("fields") == "uuid", "list requests fields=uuid only")
	ck(t, log[1].method == "POST" && log[1].path == cgPath, "creation POSTs the collection")
	ck(t, query(t, log[1]).Get("return_timeout") == "0", "POST uses return_timeout=0")
	ck(t, reflect.DeepEqual(log[1].body, expectedCreateBody()),
		"POST body matches the pinned contract exactly")
	ck(t, log[2].raw == jobHref1 && log[3].raw == jobHref1 && log[4].raw == jobHref1,
		"job polled on the exact _links.self.href")
	ck(t, log[5].method == "GET" && log[5].path == cgPath, "UUID re-resolved by listing after the job")
	ck(t, log[6].method == "GET" && log[6].path == instancePath, "final state read from the instance")
	ck(t, query(t, log[6]).Get("fields") == "**", "final read asks for fields=**")
	ck(t, report.Created && !report.Updated && !report.NoChange, "report says created")
	ck(t, report.UUID == "cg-uuid-77", "report carries the CG uuid")
	ck(t, report.JobPolls == 3, "three job polls counted")
	ck(t, report.Restarts == 0, "no restarts on the clean path")
	ck(t, reflect.DeepEqual(*sleeps, []time.Duration{time.Second, time.Second}),
		"sleep(1s) between polls only")
	_, hasTags := report.Final["_tags"]
	_, hasSpace := report.Final["space"]
	_, hasRepl := report.Final["replication_relationships"]
	ck(t, hasTags && hasSpace && hasRepl, "unknown fields preserved in the final document")
	ck(t, report.Final["uuid"] == "cg-uuid-77", "final document is the instance read")
}

func TestNoChange(t *testing.T) {
	mock, rec, sleeps := setup(t, map[string][]mockResp{
		"GET " + cgPath:       {{200, foundList}},
		"GET " + instancePath: {{200, docInSync}},
	})
	report, err := rec.Reconcile(context.Background(), spec())
	if err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	log := mock.requests()
	ck(t, len(log) == 2, "in-sync CG needs exactly 2 requests (list + instance)")
	ck(t, log[0].method == "GET" && log[1].method == "GET", "no write requests when in sync")
	ck(t, report.NoChange && !report.Created && !report.Updated, "report says no change")
	ck(t, report.UUID == "cg-uuid-77", "report carries the CG uuid")
	ck(t, report.JobPolls == 0, "no jobs when nothing changes")
	ck(t, len(*sleeps) == 0, "no pacing when nothing changes")
	_, hasTags := report.Final["_tags"]
	ck(t, hasTags, "unknown fields preserved on the no-change path")
}

func TestUpdateFlow(t *testing.T) {
	mock, rec, sleeps := setup(t, map[string][]mockResp{
		"GET " + cgPath:                  {{200, foundList}},
		"GET " + instancePath:            {{200, docDrifted}, {200, docUpdated}},
		"PATCH " + instancePath:          {{202, jobAccepted("job-cg-2", jobHref2)}},
		"GET /api/cluster/jobs/job-cg-2": {{200, jobState("job-cg-2", "success")}},
	})
	report, err := rec.Reconcile(context.Background(), spec())
	if err != nil {
		t.Fatalf("reconcile: %v", err)
	}
	log := mock.requests()
	ck(t, len(log) == 5, "update flow issues exactly 5 requests")
	ck(t, log[2].method == "PATCH" && log[2].path == instancePath, "drift PATCHes the instance")
	ck(t, query(t, log[2]).Get("return_timeout") == "0", "PATCH uses return_timeout=0")
	wantPatch := map[string]any{
		"volumes": []any{
			map[string]any{"name": "vol_app_logs", "provisioning_options": map[string]any{"action": "add"}},
		},
		"qos": map[string]any{"policy": map[string]any{"name": "gold-tier"}},
	}
	ck(t, reflect.DeepEqual(log[2].body, wantPatch),
		"PATCH body carries only the owned drifted fields")
	_, hasName := log[2].body["name"]
	_, hasSVM := log[2].body["svm"]
	_, hasSpace := log[2].body["space"]
	_, patchTags := log[2].body["_tags"]
	ck(t, !hasName && !hasSVM && !hasSpace && !patchTags,
		"PATCH never echoes identity or unknown fields")
	ck(t, log[3].raw == jobHref2, "PATCH job polled on its exact href")
	ck(t, log[4].path == instancePath && query(t, log[4]).Get("fields") == "**",
		"final state re-read after the update")
	ck(t, report.Updated && !report.Created && !report.NoChange, "report says updated")
	ck(t, report.JobPolls == 1, "single job poll counted")
	ck(t, len(*sleeps) == 0, "no sleep when the first poll is terminal")
	_, hasTags := report.Final["_tags"]
	ck(t, hasTags, "unknown fields preserved after update")
	qos, _ := report.Final["qos"].(map[string]any)
	policy, _ := qos["policy"].(map[string]any)
	ck(t, policy["name"] == "gold-tier", "final document reflects the applied qos policy")
}

func TestCreateRaceFallsBackToUpdatePath(t *testing.T) {
	mock, rec, _ := setup(t, map[string][]mockResp{
		"GET " + cgPath:  {{200, emptyList}, {200, foundList}},
		"POST " + cgPath: {{202, jobAccepted("job-cg-3", jobHref3)}},
		"GET /api/cluster/jobs/job-cg-3": {
			{200, jobFailed("job-cg-3", "53411860", "An object with the same identifier in the same scope exists")},
		},
		"GET " + instancePath: {{200, docInSync}},
	})
	report, err := rec.Reconcile(context.Background(), spec())
	if err != nil {
		t.Fatalf("reconcile after create race: %v", err)
	}
	log := mock.requests()
	ck(t, len(log) == 5, "race flow: list, POST, job, list, instance")
	ck(t, log[1].method == "POST", "creation attempted first")
	ck(t, log[3].method == "GET" && log[3].path == cgPath, "identifier conflict re-lists by name")
	ck(t, report.Restarts == 1, "identifier conflict counts one restart")
	ck(t, !report.Created && report.NoChange, "lost race converges without claiming creation")
	ck(t, report.UUID == "cg-uuid-77", "existing uuid adopted after the race")
}

func TestInstanceDeletedUnderneath(t *testing.T) {
	mock, rec, _ := setup(t, map[string][]mockResp{
		"GET " + cgPath: {{200, foundList}, {200, emptyList}, {200, foundList}},
		"GET " + instancePath: {
			{404, apiErrorBody("53411842", "Consistency group does not exist.")},
			{200, docInSync},
		},
		"POST " + cgPath:                 {{202, jobAccepted("job-cg-1", jobHref1)}},
		"GET /api/cluster/jobs/job-cg-1": {{200, jobState("job-cg-1", "success")}},
	})
	report, err := rec.Reconcile(context.Background(), spec())
	if err != nil {
		t.Fatalf("reconcile after concurrent delete: %v", err)
	}
	log := mock.requests()
	ck(t, len(log) == 7, "deleted-underneath flow issues 7 requests")
	ck(t, report.Restarts == 1, "instance 404 with code 53411842 restarts once")
	ck(t, report.Created, "reconciler recreates the deleted group")
	ck(t, report.JobPolls == 1, "recreation job polled once")
}

func TestPatchJobFailureSurfaces(t *testing.T) {
	mock, rec, _ := setup(t, map[string][]mockResp{
		"GET " + cgPath:         {{200, foundList}},
		"GET " + instancePath:   {{200, docDrifted}},
		"PATCH " + instancePath: {{202, jobAccepted("job-cg-4", jobHref4)}},
		"GET /api/cluster/jobs/job-cg-4": {
			{200, jobFailed("job-cg-4", "53411853", "Fields provided in the request conflict with each other.")},
		},
	})
	_, err := rec.Reconcile(context.Background(), spec())
	ck(t, err != nil, "failed PATCH job returns an error")
	var jobErr *JobError
	ck(t, errors.As(err, &jobErr), "error is a *JobError")
	ck(t, jobErr != nil && jobErr.Code == "53411853", "job error carries the ONTAP code")
	ck(t, jobErr != nil && strings.Contains(jobErr.Message, "conflict"), "job error carries the message")
	log := mock.requests()
	ck(t, len(log) == 4, "no further requests after a failed PATCH job")
	ck(t, log[len(log)-1].raw == jobHref4, "last request is the job poll")
}

func TestAPIErrorDecode(t *testing.T) {
	mock, rec, _ := setup(t, map[string][]mockResp{
		"GET " + cgPath: {{403, apiErrorBody("6", "not authorized for that command")}},
	})
	_, err := rec.Reconcile(context.Background(), spec())
	ck(t, err != nil, "403 on the list surfaces an error")
	var apiErr *APIError
	ck(t, errors.As(err, &apiErr), "error is an *APIError")
	ck(t, apiErr != nil && apiErr.Status == 403, "APIError keeps the HTTP status")
	ck(t, apiErr != nil && apiErr.Code == "6", "APIError decodes the ONTAP code")
	ck(t, apiErr != nil && strings.Contains(apiErr.Message, "not authorized"), "APIError keeps the message")
	ck(t, err != nil && strings.Contains(err.Error(), "6") && strings.Contains(err.Error(), "not authorized"),
		"rendered error names code and message")
	ck(t, err != nil && !strings.Contains(err.Error(), testPass), "rendered error never contains the password")
	ck(t, len(mock.requests()) == 1, "no retries after a permission error")
}

func TestPollBudget(t *testing.T) {
	mock, rec, sleeps := setup(t, map[string][]mockResp{
		"GET " + cgPath:  {{200, emptyList}},
		"POST " + cgPath: {{202, jobAccepted("job-cg-5", jobHref5)}},
		"GET /api/cluster/jobs/job-cg-5": {
			{200, jobState("job-cg-5", "running")},
			{200, jobState("job-cg-5", "running")},
		},
	})
	rec.MaxPolls = 2
	_, err := rec.Reconcile(context.Background(), spec())
	ck(t, err != nil && strings.Contains(err.Error(), "poll budget") && strings.Contains(err.Error(), "2"),
		"exhausted poll budget names the bound")
	polls := 0
	for _, req := range mock.requests() {
		if strings.HasPrefix(req.path, "/api/cluster/jobs/") {
			polls++
		}
	}
	ck(t, polls == 2, "exactly MaxPolls polls issued")
	ck(t, reflect.DeepEqual(*sleeps, []time.Duration{time.Second}), "sleeps only between polls")
}

func TestRestartBudget(t *testing.T) {
	mock, rec, _ := setup(t, map[string][]mockResp{
		"GET " + cgPath: {{200, emptyList}, {200, emptyList}},
		"POST " + cgPath: {
			{202, jobAccepted("job-cg-3", jobHref3)},
			{202, jobAccepted("job-cg-4", jobHref4)},
		},
		"GET /api/cluster/jobs/job-cg-3": {
			{200, jobFailed("job-cg-3", "53411860", "An object with the same identifier in the same scope exists")},
		},
		"GET /api/cluster/jobs/job-cg-4": {
			{200, jobFailed("job-cg-4", "53411860", "An object with the same identifier in the same scope exists")},
		},
	})
	_, err := rec.Reconcile(context.Background(), spec())
	ck(t, err != nil && strings.Contains(err.Error(), "restart budget"),
		"second restartable conflict exhausts the restart budget")
	ck(t, len(mock.requests()) == 6, "restart budget flow issues 6 requests")
}
