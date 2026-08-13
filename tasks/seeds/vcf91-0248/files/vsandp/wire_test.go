package vsandp

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"testing"
	"time"
)

// These tests are the contract check. They assert the exact shape of every
// request the client puts on the wire against docs/contract.json, and they
// assert that a session token expiring mid-batch costs one refresh and zero
// repeated work. Everything runs against the in-memory mock in mock_test.go; no
// live VMware endpoint is contacted and no network socket is opened.

const testBootstrap = "bootstrap-token"

func newTestClient(t *testing.T, m *mockAppliance) *Client {
	t.Helper()
	c, err := New(Config{
		BaseURL:        m.baseURL(),
		BootstrapToken: testBootstrap,
		HTTPClient:     m.httpClient,
	})
	if err != nil {
		t.Fatalf("New: unexpected error: %v", err)
	}
	if c == nil {
		t.Fatal("New: returned a nil client and a nil error")
	}
	return c
}

func testContext(t *testing.T) context.Context {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	t.Cleanup(cancel)
	return ctx
}

func compactJSON(t *testing.T, raw []byte) string {
	t.Helper()
	var buf bytes.Buffer
	if err := json.Compact(&buf, raw); err != nil {
		t.Fatalf("request body is not valid JSON (%v): %q", err, raw)
	}
	return buf.String()
}

// wireWant describes the request shape a batch must produce.
type wireWant struct {
	cluster        string
	listQuery      string // exact RawQuery of the protection group list request
	createBody     string // exact compacted JSON body of every snapshot create
	sessionCreates int
}

// assertWire walks the mock's request log and checks every operation against
// the pinned contract. It assumes the batch ran on a single goroutine, so the
// log is in causal order.
func assertWire(t *testing.T, m *mockAppliance, want wireWant) {
	t.Helper()
	reqs := m.requests()
	if len(reqs) == 0 {
		t.Fatal("the client sent no requests at all")
	}
	if n := m.unknownRoutes(); n != 0 {
		t.Errorf("client called %d endpoint(s) that the contract does not name", n)
	}

	minted := m.mintedTokens()
	if len(minted) != want.sessionCreates {
		t.Errorf("Snapservice.Sessions_create invoked %d time(s), want %d",
			len(minted), want.sessionCreates)
	}

	listPath := "/snapservice/clusters/" + want.cluster + "/protection-groups"
	sessionsSoFar := 0
	lists := 0
	creates := 0

	for i, r := range reqs {
		where := fmt.Sprintf("request %d (%s %s?%s)", i, r.Method, r.Path, r.RawQuery)

		if _, ok := r.Header["Authorization"]; ok {
			t.Errorf("%s: sent an Authorization header; the contract's only security "+
				"scheme is the %s header", where, sessionHeader)
		}
		if got := r.Header.Get("Accept"); got != "application/json" {
			t.Errorf("%s: Accept = %q, want %q", where, got, "application/json")
		}
		tok, ok := r.Session()
		if !ok {
			t.Errorf("%s: missing the %s header", where, sessionHeader)
		} else if n := len(r.Header[http.CanonicalHeaderKey(sessionHeader)]); n != 1 {
			t.Errorf("%s: %s sent %d times, want once", where, sessionHeader, n)
		}
		if r.Method == http.MethodGet {
			if len(r.Body) != 0 {
				t.Errorf("%s: GET carried a %d byte body", where, len(r.Body))
			}
			if ct := r.Header.Get("Content-Type"); ct != "" {
				t.Errorf("%s: bodyless request set Content-Type %q", where, ct)
			}
		}

		switch {
		case r.IsSessionCreate():
			sessionsSoFar++
			if tok != testBootstrap {
				t.Errorf("%s: presented %q, want the bootstrap token", where, tok)
			}
			if r.RawQuery != "" {
				t.Errorf("%s: RawQuery = %q, want empty", where, r.RawQuery)
			}
			if len(r.Body) != 0 {
				t.Errorf("%s: carried a body of %d bytes, want none", where, len(r.Body))
			}
			if ct := r.Header.Get("Content-Type"); ct != "" {
				t.Errorf("%s: bodyless request set Content-Type %q", where, ct)
			}
			continue

		case r.Path == listPath && r.Method == http.MethodGet:
			lists++
			if r.RawQuery != want.listQuery {
				t.Errorf("%s: RawQuery = %q, want %q", where, r.RawQuery, want.listQuery)
			}

		case r.IsSnapshotCreate():
			creates++
			prefix := listPath + "/"
			if !strings.HasPrefix(r.Path, prefix) || !strings.HasSuffix(r.Path, "/snapshots") {
				t.Errorf("%s: path does not match the contract's snapshot create path", where)
			}
			if r.RawQuery != "vmw-task=true" {
				t.Errorf("%s: RawQuery = %q, want %q; the operation is only exposed "+
					"as a task", where, r.RawQuery, "vmw-task=true")
			}
			if ct := r.Header.Get("Content-Type"); ct != "application/json" {
				t.Errorf("%s: Content-Type = %q, want application/json", where, ct)
			}
			if got := compactJSON(t, r.Body); got != want.createBody {
				t.Errorf("%s: body = %s, want %s", where, got, want.createBody)
			}
			if !strings.Contains(want.createBody, "retention") &&
				bytes.Contains(r.Body, []byte("retention")) {
				t.Errorf("%s: unset optional property retention was serialized "+
					"instead of omitted: %s", where, r.Body)
			}

		case strings.HasPrefix(r.Path, "/snapservice/tasks/") && r.Method == http.MethodGet:
			if r.RawQuery != "" {
				t.Errorf("%s: RawQuery = %q, want empty", where, r.RawQuery)
			}

		default:
			t.Errorf("%s: does not match any operation in the pinned contract", where)
			continue
		}

		// Every non-session request must carry the newest working token.
		if sessionsSoFar == 0 {
			t.Errorf("%s: sent before any session token was minted", where)
		} else if fresh := minted[sessionsSoFar-1]; tok != fresh {
			t.Errorf("%s: presented %q, want the current working token %q",
				where, tok, fresh)
		}
	}

	if lists != 1 {
		t.Errorf("Snapservice.Clusters.ProtectionGroups_list served %d time(s), want 1", lists)
	}
	if creates == 0 {
		t.Error("no snapshot create request reached the appliance")
	}

	assertRefreshDiscipline(t, reqs)
}

// assertRefreshDiscipline checks that each rejected request is followed by
// exactly one session refresh and then by a byte-identical replay of the
// rejected request, and that no refresh happens for any other reason.
func assertRefreshDiscipline(t *testing.T, reqs []recordedRequest) {
	t.Helper()
	rejected := 0
	for i, r := range reqs {
		if r.Status != http.StatusUnauthorized || i == len(reqs)-1 {
			continue
		}
		rejected++
		next := reqs[i+1]
		if !next.IsSessionCreate() {
			t.Fatalf("request %d was rejected with 401 but request %d is %s %s, not a "+
				"session refresh", i, i+1, next.Method, next.Path)
		}
		if i+2 >= len(reqs) {
			t.Fatalf("request %d was rejected with 401 and refreshed, but the rejected "+
				"work was never replayed", i)
		}
		replay := reqs[i+2]
		if replay.Method != r.Method || replay.Path != r.Path ||
			replay.RawQuery != r.RawQuery || !bytes.Equal(replay.Body, r.Body) {
			t.Errorf("after refreshing, the client sent %s %s?%s instead of replaying the "+
				"rejected %s %s?%s", replay.Method, replay.Path, replay.RawQuery,
				r.Method, r.Path, r.RawQuery)
		}
	}
	sessions := 0
	for _, r := range reqs {
		if r.IsSessionCreate() {
			sessions++
		}
	}
	if want := rejected + 1; sessions != want {
		t.Errorf("minted %d session token(s) for %d rejected request(s), want %d: a token "+
			"must be minted once up front and once per expiry, never per request",
			sessions, rejected, want)
	}
}

func assertResults(t *testing.T, got []Result, want []Result) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("got %d result(s) %+v, want %d %+v", len(got), got, len(want), want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("result %d = %+v, want %+v", i, got[i], want[i])
		}
	}
}

// assertSequentialBatch verifies the operation ordering promised by
// SnapshotProtectionGroups: each create task reaches a terminal state before
// the client starts the next protection group. It also checks every dynamic
// path segment against url.PathEscape.
func assertSequentialBatch(t *testing.T, m *mockAppliance, cluster string, want []Result) {
	t.Helper()
	reqs := m.requests()
	next := 0
	activeTask := ""
	for i, r := range reqs {
		switch {
		case r.IsSnapshotCreate() && r.Status == http.StatusAccepted:
			if activeTask != "" {
				t.Fatalf("request %d started another snapshot while task %q was still active", i, activeTask)
			}
			if next >= len(want) {
				t.Fatalf("request %d created an unexpected extra snapshot", i)
			}
			expectedPath := "/snapservice/clusters/" + url.PathEscape(cluster) +
				"/protection-groups/" + url.PathEscape(want[next].PG) + "/snapshots"
			if r.Path != expectedPath {
				t.Errorf("request %d path = %q, want %q", i, r.Path, expectedPath)
			}
			var task string
			if err := json.Unmarshal(r.Response, &task); err != nil {
				t.Fatalf("request %d: mock returned an invalid task identifier: %v", i, err)
			}
			if task != want[next].Task {
				t.Errorf("request %d task = %q, want %q", i, task, want[next].Task)
			}
			activeTask = task

		case strings.HasPrefix(r.Path, "/snapservice/tasks/") && r.Status == http.StatusOK:
			if activeTask == "" {
				t.Fatalf("request %d polled a task while no snapshot task was active", i)
			}
			expectedPath := "/snapservice/tasks/" + url.PathEscape(activeTask)
			if r.Path != expectedPath {
				t.Errorf("request %d path = %q, want %q", i, r.Path, expectedPath)
			}
			var info struct {
				Status string `json:"status"`
			}
			if err := json.Unmarshal(r.Response, &info); err != nil {
				t.Fatalf("request %d: mock returned invalid task info: %v", i, err)
			}
			if info.Status == StatusSucceeded || info.Status == StatusFailed {
				activeTask = ""
				next++
			}
		}
	}
	if activeTask != "" || next != len(want) {
		t.Fatalf("batch completed %d of %d protection groups; active task %q", next, len(want), activeTask)
	}
}

func TestSnapshotProtectionGroups(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name        string
		cluster     string
		fixtures    []pgFixture
		expireAfter int
		polls       int
		req         BatchRequest
		want        []Result
		wire        wireWant
	}{
		{
			name:     "every protection group, no retention",
			cluster:  "domain-c8",
			fixtures: []pgFixture{{ID: "pg-1", Name: "alpha"}, {ID: "pg-2", Name: "bravo"}},
			polls:    2,
			req:      BatchRequest{Cluster: "domain-c8", SnapshotName: "pre-upgrade"},
			want: []Result{
				{PG: "pg-1", Name: "alpha", Task: "task-1", Status: StatusSucceeded},
				{PG: "pg-2", Name: "bravo", Task: "task-2", Status: StatusSucceeded},
			},
			wire: wireWant{
				cluster:        "domain-c8",
				listQuery:      "",
				createBody:     `{"name":"pre-upgrade"}`,
				sessionCreates: 1,
			},
		},
		{
			name:     "name filter and retention",
			cluster:  "domain-c8",
			fixtures: []pgFixture{{ID: "pg-1", Name: "alpha"}, {ID: "pg-2", Name: "bravo"}},
			polls:    1,
			req: BatchRequest{
				Cluster:      "domain-c8",
				Names:        []string{"bravo", "alpha"},
				SnapshotName: "nightly",
				Retention:    &RetentionPeriod{Unit: UnitDay, Duration: 7},
			},
			want: []Result{
				{PG: "pg-1", Name: "alpha", Task: "task-1", Status: StatusSucceeded},
				{PG: "pg-2", Name: "bravo", Task: "task-2", Status: StatusSucceeded},
			},
			wire: wireWant{
				cluster:        "domain-c8",
				listQuery:      "names=bravo&names=alpha",
				createBody:     `{"name":"nightly","retention":{"unit":"DAY","duration":7}}`,
				sessionCreates: 1,
			},
		},
		{
			name:    "token expires while starting a snapshot",
			cluster: "domain-c8",
			fixtures: []pgFixture{
				{ID: "pg-1", Name: "alpha"}, {ID: "pg-2", Name: "bravo"}, {ID: "pg-3", Name: "charlie"},
			},
			expireAfter: 4, // list, create pg-1, two polls; then the pg-2 create is rejected
			polls:       2,
			req:         BatchRequest{Cluster: "domain-c8", SnapshotName: "pre-upgrade"},
			want: []Result{
				{PG: "pg-1", Name: "alpha", Task: "task-1", Status: StatusSucceeded},
				{PG: "pg-2", Name: "bravo", Task: "task-2", Status: StatusSucceeded},
				{PG: "pg-3", Name: "charlie", Task: "task-3", Status: StatusSucceeded},
			},
			wire: wireWant{
				cluster:        "domain-c8",
				listQuery:      "",
				createBody:     `{"name":"pre-upgrade"}`,
				sessionCreates: 2,
			},
		},
		{
			name:        "token expires while polling a task",
			cluster:     "domain-c8",
			fixtures:    []pgFixture{{ID: "pg-1", Name: "alpha"}, {ID: "pg-2", Name: "bravo"}},
			expireAfter: 2, // list, create pg-1; then the first poll is rejected
			polls:       2,
			req: BatchRequest{
				Cluster:      "domain-c8",
				SnapshotName: "nightly",
				Retention:    &RetentionPeriod{Unit: UnitHour, Duration: 12},
			},
			want: []Result{
				{PG: "pg-1", Name: "alpha", Task: "task-1", Status: StatusSucceeded},
				{PG: "pg-2", Name: "bravo", Task: "task-2", Status: StatusSucceeded},
			},
			wire: wireWant{
				cluster:        "domain-c8",
				listQuery:      "",
				createBody:     `{"name":"nightly","retention":{"unit":"HOUR","duration":12}}`,
				sessionCreates: 2,
			},
		},
		{
			name:     "a failed task is reported, not an error",
			cluster:  "domain-c8",
			fixtures: []pgFixture{{ID: "pg-1", Name: "alpha", Fail: true}, {ID: "pg-2", Name: "bravo"}},
			polls:    2,
			req:      BatchRequest{Cluster: "domain-c8", SnapshotName: "pre-upgrade"},
			want: []Result{
				{PG: "pg-1", Name: "alpha", Task: "task-1", Status: StatusFailed},
				{PG: "pg-2", Name: "bravo", Task: "task-2", Status: StatusSucceeded},
			},
			wire: wireWant{
				cluster:        "domain-c8",
				listQuery:      "",
				createBody:     `{"name":"pre-upgrade"}`,
				sessionCreates: 1,
			},
		},
		{
			name:        "token expires and the batch has one protection group left",
			cluster:     "domain-c8",
			fixtures:    []pgFixture{{ID: "pg-1", Name: "alpha"}},
			expireAfter: 1, // the list succeeds; the create is rejected
			polls:       3,
			req:         BatchRequest{Cluster: "domain-c8", Names: []string{"alpha"}, SnapshotName: "adhoc"},
			want: []Result{
				{PG: "pg-1", Name: "alpha", Task: "task-1", Status: StatusSucceeded},
			},
			wire: wireWant{
				cluster:        "domain-c8",
				listQuery:      "names=alpha",
				createBody:     `{"name":"adhoc"}`,
				sessionCreates: 2,
			},
		},
		{
			name:    "percent-encodes path and query identifiers",
			cluster: "domain/c 8?%",
			fixtures: []pgFixture{{
				ID: "pg/a b?%", Name: "alpha/beta ?%", TaskID: "task/1 ?%",
			}},
			polls: 1,
			req: BatchRequest{
				Cluster:      "domain/c 8?%",
				Names:        []string{"alpha/beta ?%"},
				SnapshotName: "adhoc",
			},
			want: []Result{{
				PG: "pg/a b?%", Name: "alpha/beta ?%", Task: "task/1 ?%", Status: StatusSucceeded,
			}},
			wire: wireWant{
				cluster:        "domain%2Fc%208%3F%25",
				listQuery:      "names=alpha%2Fbeta+%3F%25",
				createBody:     `{"name":"adhoc"}`,
				sessionCreates: 1,
			},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			m := newMockAppliance(t, mockConfig{
				bootstrap:     testBootstrap,
				clusters:      map[string][]pgFixture{tc.cluster: tc.fixtures},
				expireAfter:   tc.expireAfter,
				pollsToFinish: tc.polls,
			})
			c := newTestClient(t, m)

			report, err := c.SnapshotProtectionGroups(testContext(t), tc.req)
			if err != nil {
				t.Fatalf("SnapshotProtectionGroups: unexpected error: %v", err)
			}
			if report == nil {
				t.Fatal("SnapshotProtectionGroups: nil report with a nil error")
			}
			assertResults(t, report.Results, tc.want)
			assertWire(t, m, tc.wire)
			assertSequentialBatch(t, m, tc.cluster, tc.want)

			// The point of refreshing rather than restarting: no protection
			// group is snapshotted twice.
			for _, r := range tc.want {
				if n := m.acceptedCreates(tc.cluster, r.PG); n != 1 {
					t.Errorf("appliance accepted %d snapshot create(s) for %s, want exactly 1; "+
						"work completed before the refresh must not be redone", n, r.PG)
				}
			}
			if got, want := c.SessionCreates(), tc.wire.sessionCreates; got != want {
				t.Errorf("Client.SessionCreates() = %d, want %d", got, want)
			}
		})
	}
}

func TestRefreshIsAttemptedOnlyOncePerRequest(t *testing.T) {
	t.Parallel()
	m := newMockAppliance(t, mockConfig{
		bootstrap:     testBootstrap,
		clusters:      map[string][]pgFixture{"domain-c8": {{ID: "pg-1", Name: "alpha"}}},
		rejectAll:     true,
		pollsToFinish: 1,
	})
	c := newTestClient(t, m)

	_, err := c.SnapshotProtectionGroups(testContext(t), BatchRequest{
		Cluster:      "domain-c8",
		SnapshotName: "pre-upgrade",
	})
	if err == nil {
		t.Fatal("SnapshotProtectionGroups: want an error when the refreshed token is also rejected")
	}
	if n := m.sessionCreates(); n != 2 {
		t.Errorf("minted %d session token(s), want 2: one up front and one refresh, then give up", n)
	}
	reqs := m.requests()
	if len(reqs) != 4 {
		t.Fatalf("sent %d request(s), want 4 (mint, list, refresh, replay): %s", len(reqs), summarize(reqs))
	}
	if reqs[1].Status != http.StatusUnauthorized || reqs[3].Status != http.StatusUnauthorized {
		t.Errorf("expected both list attempts to be rejected: %s", summarize(reqs))
	}
	if !bytes.Equal(reqs[1].Body, reqs[3].Body) || reqs[1].Path != reqs[3].Path ||
		reqs[1].RawQuery != reqs[3].RawQuery {
		t.Errorf("the replayed request differs from the rejected one: %s", summarize(reqs))
	}
}

func TestNonAuthFailureAbortsTheBatch(t *testing.T) {
	t.Parallel()
	m := newMockAppliance(t, mockConfig{
		bootstrap:     testBootstrap,
		clusters:      map[string][]pgFixture{"domain-c8": {{ID: "pg-1", Name: "alpha"}}},
		pollsToFinish: 1,
	})
	c := newTestClient(t, m)

	_, err := c.SnapshotProtectionGroups(testContext(t), BatchRequest{
		Cluster:      "domain-c404",
		SnapshotName: "pre-upgrade",
	})
	if err == nil {
		t.Fatal("SnapshotProtectionGroups: want an error when the appliance answers 404")
	}
	if n := m.sessionCreates(); n != 1 {
		t.Errorf("minted %d session token(s), want 1: a 404 is not an authentication problem", n)
	}
	if n := m.unknownRoutes(); n != 0 {
		t.Errorf("client called %d endpoint(s) outside the pinned contract", n)
	}
}

func TestEmptyProtectionGroupListReturnsEmptyReport(t *testing.T) {
	t.Parallel()
	m := newMockAppliance(t, mockConfig{
		bootstrap:     testBootstrap,
		clusters:      map[string][]pgFixture{"domain-c8": {}},
		pollsToFinish: 1,
	})
	c := newTestClient(t, m)
	report, err := c.SnapshotProtectionGroups(testContext(t), BatchRequest{
		Cluster: "domain-c8", SnapshotName: "adhoc",
	})
	if err != nil {
		t.Fatalf("SnapshotProtectionGroups: unexpected error: %v", err)
	}
	if report == nil || len(report.Results) != 0 {
		t.Fatalf("SnapshotProtectionGroups: report = %+v, want an empty report", report)
	}
	reqs := m.requests()
	if len(reqs) != 2 || !reqs[0].IsSessionCreate() ||
		reqs[1].Method != http.MethodGet || reqs[1].Status != http.StatusOK {
		t.Fatalf("requests = %s, want one session create followed by one list", summarize(reqs))
	}
	if n := m.sessionCreates(); n != 1 {
		t.Errorf("minted %d session token(s), want 1", n)
	}
}

func TestConcurrentBatchesShareOneRefresh(t *testing.T) {
	t.Parallel()
	clusters := map[string][]pgFixture{
		"domain-c8": {{ID: "pg-1", Name: "alpha"}, {ID: "pg-2", Name: "bravo"}},
		"domain-c9": {{ID: "pg-3", Name: "charlie"}, {ID: "pg-4", Name: "delta"}},
	}
	m := newMockAppliance(t, mockConfig{
		bootstrap:                testBootstrap,
		clusters:                 clusters,
		expireAfter:              3,
		pollsToFinish:            2,
		simultaneousUnauthorized: 2,
	})
	c := newTestClient(t, m)

	var wg sync.WaitGroup
	reports := make([]*BatchReport, 2)
	errs := make([]error, 2)
	names := []string{"domain-c8", "domain-c9"}
	for i, cluster := range names {
		wg.Add(1)
		go func(i int, cluster string) {
			defer wg.Done()
			reports[i], errs[i] = c.SnapshotProtectionGroups(testContext(t), BatchRequest{
				Cluster:      cluster,
				SnapshotName: "pre-upgrade",
			})
		}(i, cluster)
	}
	wg.Wait()

	for i, err := range errs {
		if err != nil {
			t.Fatalf("batch %d: unexpected error: %v", i, err)
		}
		if len(reports[i].Results) != 2 {
			t.Fatalf("batch %d: got %d result(s), want 2", i, len(reports[i].Results))
		}
		for _, r := range reports[i].Results {
			if r.Status != StatusSucceeded {
				t.Errorf("batch %d: %s status = %s, want %s", i, r.PG, r.Status, StatusSucceeded)
			}
		}
	}
	for cluster, fixtures := range clusters {
		for _, f := range fixtures {
			if n := m.acceptedCreates(cluster, f.ID); n != 1 {
				t.Errorf("appliance accepted %d snapshot create(s) for %s, want exactly 1", n, f.ID)
			}
		}
	}
	if n := m.sessionCreates(); n != 2 {
		t.Errorf("minted %d session token(s), want 2: concurrent batches must share the "+
			"token and the single refresh that follows one expiry", n)
	}
	if got := c.SessionCreates(); got != 2 {
		t.Errorf("Client.SessionCreates() = %d, want 2", got)
	}
	if n := m.unknownRoutes(); n != 0 {
		t.Errorf("client called %d endpoint(s) outside the pinned contract", n)
	}
}

func TestRejectsInvalidConfigAndRequests(t *testing.T) {
	t.Parallel()

	configCases := []struct {
		name string
		cfg  Config
	}{
		{"no base URL", Config{BootstrapToken: testBootstrap}},
		{"no bootstrap token", Config{BaseURL: "http://127.0.0.1:1"}},
	}
	for _, tc := range configCases {
		t.Run("config/"+tc.name, func(t *testing.T) {
			t.Parallel()
			if _, err := New(tc.cfg); err == nil {
				t.Fatalf("New(%+v): want an error", tc.cfg)
			}
		})
	}

	requestCases := []struct {
		name string
		req  BatchRequest
	}{
		{"no cluster", BatchRequest{SnapshotName: "adhoc"}},
		{"no snapshot name", BatchRequest{Cluster: "domain-c8"}},
		{"empty name filter entry", BatchRequest{
			Cluster: "domain-c8", SnapshotName: "adhoc", Names: []string{"alpha", ""}}},
		{"duplicate name filter entry", BatchRequest{
			Cluster: "domain-c8", SnapshotName: "adhoc", Names: []string{"alpha", "alpha"}}},
		{"retention without a unit", BatchRequest{
			Cluster: "domain-c8", SnapshotName: "adhoc",
			Retention: &RetentionPeriod{Duration: 7}}},
		{"retention without a duration", BatchRequest{
			Cluster: "domain-c8", SnapshotName: "adhoc",
			Retention: &RetentionPeriod{Unit: UnitDay}}},
		{"retention with a negative duration", BatchRequest{
			Cluster: "domain-c8", SnapshotName: "adhoc",
			Retention: &RetentionPeriod{Unit: UnitDay, Duration: -1}}},
	}
	for _, tc := range requestCases {
		t.Run("request/"+tc.name, func(t *testing.T) {
			t.Parallel()
			m := newMockAppliance(t, mockConfig{
				bootstrap:     testBootstrap,
				clusters:      map[string][]pgFixture{"domain-c8": {{ID: "pg-1", Name: "alpha"}}},
				pollsToFinish: 1,
			})
			c := newTestClient(t, m)
			if _, err := c.SnapshotProtectionGroups(testContext(t), tc.req); err == nil {
				t.Fatalf("SnapshotProtectionGroups(%+v): want an error", tc.req)
			}
			if reqs := m.requests(); len(reqs) != 0 {
				t.Errorf("an invalid request reached the appliance: %s", summarize(reqs))
			}
		})
	}
}

func TestMalformedResponsesAbortTheBatch(t *testing.T) {
	t.Parallel()
	for _, operation := range []string{"session", "list", "create", "task"} {
		t.Run(operation, func(t *testing.T) {
			t.Parallel()
			m := newMockAppliance(t, mockConfig{
				bootstrap:         testBootstrap,
				clusters:          map[string][]pgFixture{"domain-c8": {{ID: "pg-1", Name: "alpha"}}},
				pollsToFinish:     1,
				malformedResponse: operation,
			})
			c := newTestClient(t, m)
			if _, err := c.SnapshotProtectionGroups(testContext(t), BatchRequest{
				Cluster: "domain-c8", SnapshotName: "adhoc",
			}); err == nil {
				t.Fatalf("SnapshotProtectionGroups: want an error for malformed %s response", operation)
			}
		})
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestTransportFailureAbortsTheBatch(t *testing.T) {
	t.Parallel()
	c, err := New(Config{
		BaseURL:        "http://127.0.0.1",
		BootstrapToken: testBootstrap,
		HTTPClient: &http.Client{Transport: roundTripFunc(func(*http.Request) (*http.Response, error) {
			return nil, errors.New("injected transport failure")
		})},
	})
	if err != nil {
		t.Fatalf("New: unexpected error: %v", err)
	}
	if _, err := c.SnapshotProtectionGroups(testContext(t), BatchRequest{
		Cluster: "domain-c8", SnapshotName: "adhoc",
	}); err == nil {
		t.Fatal("SnapshotProtectionGroups: want an error for a transport failure")
	}
}

func TestPreservesExportedCompatibilitySymbol(t *testing.T) {
	t.Parallel()
	if ErrNotImplemented == nil {
		t.Fatal("ErrNotImplemented must remain available for exported API compatibility")
	}
}

func summarize(reqs []recordedRequest) string {
	var b strings.Builder
	for i, r := range reqs {
		fmt.Fprintf(&b, "\n  %d: %s %s?%s -> %d", i, r.Method, r.Path, r.RawQuery, r.Status)
	}
	return b.String()
}
