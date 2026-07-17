// Protected acceptance tests for the Resource Graph export client.
// Hermetic: httptest plays the ARG endpoint; the bearer token is a dummy.
package rgexport

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"sync"
	"testing"
	"time"
)

const testToken = "dummy-arg-token"

var testSubs = []string{
	"11111111-2222-3333-4444-555555555555",
	"aaaabbbb-cccc-dddd-eeee-ffff00001111",
}

type recordedReq struct {
	method      string
	path        string
	rawQuery    string
	auth        string
	contentType string
	body        map[string]any
}

type scriptedResp struct {
	status  int
	headers map[string]string
	body    string
}

type mockGraph struct {
	t      *testing.T
	mu     sync.Mutex
	reqs   []recordedReq
	resps  []scriptedResp
	server *httptest.Server
}

func newMockGraph(t *testing.T, resps ...scriptedResp) *mockGraph {
	t.Helper()
	m := &mockGraph{t: t, resps: resps}
	m.server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, err := io.ReadAll(r.Body)
		if err != nil {
			t.Errorf("reading request body: %v", err)
		}
		var body map[string]any
		if len(raw) > 0 {
			if err := json.Unmarshal(raw, &body); err != nil {
				t.Errorf("request body is not JSON: %v: %s", err, raw)
			}
		}
		m.mu.Lock()
		m.reqs = append(m.reqs, recordedReq{
			method:      r.Method,
			path:        r.URL.Path,
			rawQuery:    r.URL.RawQuery,
			auth:        r.Header.Get("Authorization"),
			contentType: r.Header.Get("Content-Type"),
			body:        body,
		})
		if len(m.resps) == 0 {
			m.mu.Unlock()
			t.Errorf("unexpected extra request #%d to %s", len(m.reqs), r.URL)
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		resp := m.resps[0]
		m.resps = m.resps[1:]
		m.mu.Unlock()
		for name, value := range resp.headers {
			w.Header().Set(name, value)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(resp.status)
		if _, err := io.WriteString(w, resp.body); err != nil {
			t.Errorf("writing response: %v", err)
		}
	}))
	t.Cleanup(m.server.Close)
	return m
}

func (m *mockGraph) requests() []recordedReq {
	m.mu.Lock()
	defer m.mu.Unlock()
	return append([]recordedReq(nil), m.reqs...)
}

func clientFor(m *mockGraph, sleeps *[]time.Duration, maxThrottleRetries int) *Client {
	return NewClient(Config{
		BaseURL:            m.server.URL,
		Token:              testToken,
		HTTPClient:         m.server.Client(),
		Sleep:              func(d time.Duration) { *sleeps = append(*sleeps, d) },
		MaxThrottleRetries: maxThrottleRetries,
	})
}

func page(rows string, totalRecords int, skipToken string) string {
	body := `{"count":0,"data":` + rows + `,"facets":[],"resultTruncated":"false","totalRecords":` +
		jsonInt(totalRecords) + `}`
	if skipToken != "" {
		body = body[:len(body)-1] + `,"$skipToken":"` + skipToken + `"}`
	}
	return body
}

func jsonInt(n int) string {
	raw, _ := json.Marshal(n)
	return string(raw)
}

func options(t *testing.T, r recordedReq) map[string]any {
	t.Helper()
	opts, ok := r.body["options"].(map[string]any)
	if !ok {
		t.Fatalf("request body has no options object: %v", r.body)
	}
	return opts
}

func rowNames(report *Report) []string {
	names := make([]string, 0, len(report.Rows))
	for _, row := range report.Rows {
		name, _ := row["name"].(string)
		names = append(names, name)
	}
	return names
}

func TestFirstPageRequestShape(t *testing.T) {
	mock := newMockGraph(t, scriptedResp{
		status: 200,
		body:   page(`[{"id":"/sub/1/vm1","name":"vm1","type":"microsoft.compute/virtualmachines"}]`, 1, ""),
	})
	var sleeps []time.Duration
	client := clientFor(mock, &sleeps, 0)

	report, err := client.QueryAll(t.Context(), Query{
		Query:         "Resources | project id, name, type | order by id asc",
		Subscriptions: testSubs,
		PageSize:      100,
	})
	if err != nil {
		t.Fatalf("QueryAll: %v", err)
	}

	reqs := mock.requests()
	if len(reqs) != 1 {
		t.Fatalf("expected exactly 1 request, got %d", len(reqs))
	}
	r := reqs[0]
	if r.method != http.MethodPost {
		t.Errorf("method = %s, want POST", r.method)
	}
	if r.path != "/providers/Microsoft.ResourceGraph/resources" {
		t.Errorf("path = %q", r.path)
	}
	if r.rawQuery != "api-version=2024-04-01" {
		t.Errorf("query = %q, want api-version=2024-04-01", r.rawQuery)
	}
	if r.auth != "Bearer "+testToken {
		t.Errorf("Authorization = %q", r.auth)
	}
	if r.contentType != "application/json" {
		t.Errorf("Content-Type = %q", r.contentType)
	}
	if got := r.body["query"]; got != "Resources | project id, name, type | order by id asc" {
		t.Errorf("body query = %v", got)
	}
	wantSubs := []any{testSubs[0], testSubs[1]}
	if got, _ := r.body["subscriptions"].([]any); !reflect.DeepEqual(got, wantSubs) {
		t.Errorf("body subscriptions = %v, want %v", got, wantSubs)
	}
	opts := options(t, r)
	if got := opts["resultFormat"]; got != "objectArray" {
		t.Errorf("options.resultFormat = %v, want objectArray", got)
	}
	if got := opts["$top"]; got != float64(100) {
		t.Errorf("options.$top = %v, want 100", got)
	}
	if _, present := opts["$skipToken"]; present {
		t.Errorf("first page must not carry $skipToken, options = %v", opts)
	}

	if want := []string{"vm1"}; !reflect.DeepEqual(rowNames(report), want) {
		t.Errorf("rows = %v, want %v", rowNames(report), want)
	}
	if report.TotalRecords != 1 || report.Pages != 1 || report.Truncated {
		t.Errorf("report = %+v", report)
	}
	if len(sleeps) != 0 {
		t.Errorf("no waits expected, got %v", sleeps)
	}
}

func TestTopOmittedWithoutPageSize(t *testing.T) {
	mock := newMockGraph(t, scriptedResp{status: 200, body: page(`[]`, 0, "")})
	var sleeps []time.Duration
	if _, err := clientFor(mock, &sleeps, 0).QueryAll(t.Context(), Query{
		Query:         "Resources | count",
		Subscriptions: testSubs,
	}); err != nil {
		t.Fatalf("QueryAll: %v", err)
	}
	opts := options(t, mock.requests()[0])
	if _, present := opts["$top"]; present {
		t.Errorf("$top must be omitted when PageSize is zero, options = %v", opts)
	}
}

func TestFollowsSkipTokenWithSameQueryAndScopes(t *testing.T) {
	mock := newMockGraph(t,
		scriptedResp{status: 200, body: page(`[{"name":"vm1"},{"name":"vm2"}]`, 5, "tok-A")},
		scriptedResp{status: 200, body: page(`[]`, 5, "tok-B")}, // empty middle page, still more data
		scriptedResp{status: 200, body: page(`[{"name":"vm3"},{"name":"vm4"},{"name":"vm5"}]`, 5, "")},
	)
	var sleeps []time.Duration
	report, err := clientFor(mock, &sleeps, 0).QueryAll(t.Context(), Query{
		Query:         "Resources | project name | order by name asc",
		Subscriptions: testSubs,
		PageSize:      2,
	})
	if err != nil {
		t.Fatalf("QueryAll: %v", err)
	}

	reqs := mock.requests()
	if len(reqs) != 3 {
		t.Fatalf("expected 3 requests, got %d", len(reqs))
	}
	if got := options(t, reqs[1])["$skipToken"]; got != "tok-A" {
		t.Errorf("page 2 $skipToken = %v, want tok-A", got)
	}
	if got := options(t, reqs[2])["$skipToken"]; got != "tok-B" {
		t.Errorf("page 3 $skipToken = %v, want tok-B", got)
	}
	for i, r := range reqs {
		if r.body["query"] != reqs[0].body["query"] {
			t.Errorf("request %d changed the query: %v", i+1, r.body["query"])
		}
		if !reflect.DeepEqual(r.body["subscriptions"], reqs[0].body["subscriptions"]) {
			t.Errorf("request %d changed the scopes: %v", i+1, r.body["subscriptions"])
		}
		if got := options(t, r)["$top"]; got != float64(2) {
			t.Errorf("request %d dropped $top: %v", i+1, got)
		}
		if r.auth != "Bearer "+testToken {
			t.Errorf("request %d Authorization = %q", i+1, r.auth)
		}
	}

	if want := []string{"vm1", "vm2", "vm3", "vm4", "vm5"}; !reflect.DeepEqual(rowNames(report), want) {
		t.Errorf("rows = %v, want %v", rowNames(report), want)
	}
	if report.Pages != 3 {
		t.Errorf("Pages = %d, want 3", report.Pages)
	}
	if report.TotalRecords != 5 {
		t.Errorf("TotalRecords = %d, want 5", report.TotalRecords)
	}
	if report.Truncated {
		t.Errorf("Truncated = true, want false")
	}
}

func TestResultTruncatedStringEnum(t *testing.T) {
	// resultTruncated is a string enum ("true"/"false"), not a JSON boolean,
	// and a truncated response carries no $skipToken.
	mock := newMockGraph(t, scriptedResp{
		status: 200,
		body:   `{"count":1,"data":[{"name":"vm1"}],"facets":[],"resultTruncated":"true","totalRecords":900}`,
	})
	var sleeps []time.Duration
	report, err := clientFor(mock, &sleeps, 0).QueryAll(t.Context(), Query{
		Query:         "Resources | project name",
		Subscriptions: testSubs,
	})
	if err != nil {
		t.Fatalf("QueryAll: %v", err)
	}
	if !report.Truncated {
		t.Errorf("Truncated = false, want true")
	}
	if report.TotalRecords != 900 {
		t.Errorf("TotalRecords = %d, want 900", report.TotalRecords)
	}
	if len(report.Rows) != 1 {
		t.Errorf("rows = %v", report.Rows)
	}
	if len(mock.requests()) != 1 {
		t.Errorf("a truncated page without $skipToken must end pagination")
	}
}

func TestRepeatedSkipTokenIsDetected(t *testing.T) {
	mock := newMockGraph(t,
		scriptedResp{status: 200, body: page(`[{"name":"vm1"}]`, 4, "loop-1")},
		scriptedResp{status: 200, body: page(`[{"name":"vm2"}]`, 4, "loop-1")}, // echoes the token we sent
	)
	var sleeps []time.Duration
	report, err := clientFor(mock, &sleeps, 0).QueryAll(t.Context(), Query{
		Query:         "Resources | project name",
		Subscriptions: testSubs,
	})
	if !errors.Is(err, ErrRepeatedSkipToken) {
		t.Fatalf("err = %v, want ErrRepeatedSkipToken", err)
	}
	if report == nil {
		t.Fatal("partial report must accompany the error")
	}
	if want := []string{"vm1", "vm2"}; !reflect.DeepEqual(rowNames(report), want) {
		t.Errorf("partial rows = %v, want %v", rowNames(report), want)
	}
	if len(mock.requests()) != 2 {
		t.Fatalf("client must stop after the repeated token, got %d requests", len(mock.requests()))
	}
}

func TestQuotaExhaustionWaitsForReset(t *testing.T) {
	mock := newMockGraph(t,
		scriptedResp{
			status: 200,
			headers: map[string]string{
				"x-ms-user-quota-remaining":   "0",
				"x-ms-user-quota-resets-after": "00:00:03",
			},
			body: page(`[{"name":"vm1"}]`, 2, "tok-A"),
		},
		scriptedResp{
			status: 200,
			headers: map[string]string{
				"x-ms-user-quota-remaining":   "0",
				"x-ms-user-quota-resets-after": "00:00:05",
			},
			body: page(`[{"name":"vm2"}]`, 2, ""),
		},
	)
	var sleeps []time.Duration
	report, err := clientFor(mock, &sleeps, 0).QueryAll(t.Context(), Query{
		Query:         "Resources | project name",
		Subscriptions: testSubs,
	})
	if err != nil {
		t.Fatalf("QueryAll: %v", err)
	}
	// One wait between the pages; none after the final page.
	if want := []time.Duration{3 * time.Second}; !reflect.DeepEqual(sleeps, want) {
		t.Errorf("sleeps = %v, want %v", sleeps, want)
	}
	if report.QuotaWaits != 1 {
		t.Errorf("QuotaWaits = %d, want 1", report.QuotaWaits)
	}
	if want := []string{"vm1", "vm2"}; !reflect.DeepEqual(rowNames(report), want) {
		t.Errorf("rows = %v", rowNames(report))
	}
}

func TestThrottled429RetriesTheIdenticalRequest(t *testing.T) {
	throttleBody := `{"error":{"code":"RateLimiting","message":"Please provide below info when asking for support","details":[{"code":"RateLimiting","message":"Client application has been throttled"}]}}`
	mock := newMockGraph(t,
		scriptedResp{status: 200, body: page(`[{"name":"vm1"}]`, 2, "tok-A")},
		scriptedResp{status: 429, headers: map[string]string{"Retry-After": "5"}, body: throttleBody},
		scriptedResp{status: 200, body: page(`[{"name":"vm2"}]`, 2, "")},
	)
	var sleeps []time.Duration
	report, err := clientFor(mock, &sleeps, 3).QueryAll(t.Context(), Query{
		Query:         "Resources | project name",
		Subscriptions: testSubs,
	})
	if err != nil {
		t.Fatalf("QueryAll: %v", err)
	}
	reqs := mock.requests()
	if len(reqs) != 3 {
		t.Fatalf("expected 3 requests (page, throttled retry pair), got %d", len(reqs))
	}
	if !reflect.DeepEqual(reqs[1].body, reqs[2].body) {
		t.Errorf("retry must resend the identical request body:\n%v\nvs\n%v", reqs[1].body, reqs[2].body)
	}
	if got := options(t, reqs[2])["$skipToken"]; got != "tok-A" {
		t.Errorf("retry lost the continuation token: %v", got)
	}
	if want := []time.Duration{5 * time.Second}; !reflect.DeepEqual(sleeps, want) {
		t.Errorf("sleeps = %v, want %v (honor Retry-After)", sleeps, want)
	}
	if want := []string{"vm1", "vm2"}; !reflect.DeepEqual(rowNames(report), want) {
		t.Errorf("rows = %v, want %v (no duplicates from the retried page)", rowNames(report), want)
	}
}

func TestThrottleRetriesAreBounded(t *testing.T) {
	throttle := scriptedResp{
		status:  429,
		headers: map[string]string{"Retry-After": "1"},
		body:    `{"error":{"code":"RateLimiting","message":"still throttled"}}`,
	}
	mock := newMockGraph(t, throttle, throttle, throttle)
	var sleeps []time.Duration
	_, err := clientFor(mock, &sleeps, 2).QueryAll(t.Context(), Query{
		Query:         "Resources | project name",
		Subscriptions: testSubs,
	})
	var graphErr *GraphError
	if !errors.As(err, &graphErr) {
		t.Fatalf("err = %v, want *GraphError", err)
	}
	if graphErr.StatusCode != 429 || graphErr.Code != "RateLimiting" {
		t.Errorf("GraphError = %+v", graphErr)
	}
	if got := len(mock.requests()); got != 3 {
		t.Errorf("expected initial call + 2 retries = 3 requests, got %d", got)
	}
}

func TestErrorBodyIsMapped(t *testing.T) {
	mock := newMockGraph(t, scriptedResp{
		status: 400,
		body: `{"error":{"code":"InvalidQuery","message":"Query validation error","details":[` +
			`{"code":"ParserFailure","message":"Syntax error at line 1"}]}}`,
	})
	var sleeps []time.Duration
	_, err := clientFor(mock, &sleeps, 3).QueryAll(t.Context(), Query{
		Query:         "Resources | oops",
		Subscriptions: testSubs,
	})
	var graphErr *GraphError
	if !errors.As(err, &graphErr) {
		t.Fatalf("err = %v, want *GraphError", err)
	}
	if graphErr.StatusCode != 400 {
		t.Errorf("StatusCode = %d", graphErr.StatusCode)
	}
	if graphErr.Code != "InvalidQuery" || graphErr.Message != "Query validation error" {
		t.Errorf("GraphError = %+v", graphErr)
	}
	if len(graphErr.Details) != 1 || graphErr.Details[0].Code != "ParserFailure" {
		t.Errorf("Details = %+v", graphErr.Details)
	}
	if got := len(mock.requests()); got != 1 {
		t.Errorf("a 400 must not be retried, got %d requests", got)
	}
	if len(sleeps) != 0 {
		t.Errorf("a 400 must not wait, got %v", sleeps)
	}
}
