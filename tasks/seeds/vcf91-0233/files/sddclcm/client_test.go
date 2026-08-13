package sddclcm_test

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"path/filepath"
	"reflect"
	"sort"
	"sync"
	"testing"
	"time"

	"vcf91.local/vcflcm/internal/mockvcf"
	"vcf91.local/vcflcm/sddclcm"
)

const testToken = "eyJhbGciOiJIUzI1NiJ9.sddc-lcm.test"

func contractPath(t *testing.T) string {
	t.Helper()
	return filepath.Join("..", "docs", "contract.json")
}

func task(id, createTime, status string) map[string]any {
	m := map[string]any{
		"id":           id,
		"name":         "vcf_91_upgrade_" + id,
		"status":       status,
		"type":         "apply",
		"resourceType": "COMPONENT",
		"cancellable":  true,
	}
	if createTime != "" {
		m["createTime"] = createTime
	}
	return m
}

// fixturePages is the paginated collection under test. It deliberately mixes
// page-local ordering, a cross-page duplicate, offset timestamps and tasks with
// no usable createTime.
func fixturePages() [][]map[string]any {
	return [][]map[string]any{
		{
			task("t-03", "2026-03-01T10:00:00Z", "SUCCEEDED"),
			task("t-01", "2026-03-01T09:00:00Z", "RUNNING"),
			task("t-09", "", "PENDING"),
		},
		{
			task("t-02", "2026-03-01T09:00:00Z", "RUNNING"),
			task("t-03", "2026-03-01T10:00:00Z", "FAILED"), // duplicate of the page 0 entry
			task("t-05", "2026-03-01T08:00:00Z", "SUCCEEDED"),
		},
		{
			task("t-04", "", "SCHEDULED"),
			task("t-07", "2026-03-01T11:00:00Z", "CANCELED"),
			task("t-08", "2026-03-01T12:30:00+04:00", "SUCCEEDED"),
		},
		{
			task("t-06", "not-a-timestamp", "PENDING"),
		},
	}
}

// wantOrder is the stable order: ascending createTime instant, tasks without a
// usable createTime last, ties broken by ascending id.
var wantOrder = []string{"t-05", "t-08", "t-01", "t-02", "t-03", "t-07", "t-04", "t-06", "t-09"}

func newClient(t *testing.T, srv *mockvcf.Server) *sddclcm.Client {
	t.Helper()
	c, err := sddclcm.NewClient(srv.BaseURL(), testToken, &http.Client{Timeout: 10 * time.Second})
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	return c
}

func ids(tasks []sddclcm.TaskSummary) []string {
	out := make([]string, len(tasks))
	for i, ts := range tasks {
		out[i] = ts.ID
	}
	return out
}

func ptrBool(b bool) *bool { return &b }

func mustTime(t *testing.T, value string) *time.Time {
	t.Helper()
	parsed, err := time.Parse(time.RFC3339, value)
	if err != nil {
		t.Fatalf("bad fixture timestamp %q: %v", value, err)
	}
	return &parsed
}

// TestGetTasksRequestWireShape pins the exact bytes the client puts on the
// wire for getTasks, including that unset optional filters are absent rather
// than present with an empty value.
func TestGetTasksRequestWireShape(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name      string
		opts      func(*testing.T) sddclcm.ListTasksOptions
		wantQuery []string
	}{
		{
			name: "no filter sends only the pagination controls",
			opts: func(*testing.T) sddclcm.ListTasksOptions {
				return sddclcm.ListTasksOptions{PageSize: 3}
			},
			wantQuery: []string{"pageNumber=0", "pageSize=3"},
		},
		{
			name: "single string filter",
			opts: func(*testing.T) sddclcm.ListTasksOptions {
				return sddclcm.ListTasksOptions{
					PageSize: 3,
					Filter:   sddclcm.TaskFilter{Status: "RUNNING"},
				}
			},
			wantQuery: []string{"pageNumber=0", "pageSize=3", "status=RUNNING"},
		},
		{
			name: "explicit false is sent, it is not an unset value",
			opts: func(*testing.T) sddclcm.ListTasksOptions {
				return sddclcm.ListTasksOptions{
					PageSize: 3,
					Filter:   sddclcm.TaskFilter{IncludeSystemTasks: ptrBool(false)},
				}
			},
			wantQuery: []string{"includeSystemTasks=false", "pageNumber=0", "pageSize=3"},
		},
		{
			name: "explicit true is sent",
			opts: func(*testing.T) sddclcm.ListTasksOptions {
				return sddclcm.ListTasksOptions{
					PageSize: 3,
					Filter:   sddclcm.TaskFilter{IncludeSystemTasks: ptrBool(true)},
				}
			},
			wantQuery: []string{"includeSystemTasks=true", "pageNumber=0", "pageSize=3"},
		},
		{
			name: "timestamps are normalised to RFC3339 in UTC",
			opts: func(t *testing.T) sddclcm.ListTasksOptions {
				return sddclcm.ListTasksOptions{
					PageSize: 3,
					Filter: sddclcm.TaskFilter{
						StartTimeGt: mustTime(t, "2026-03-01T05:30:00+05:30"),
						EndTimeLt:   mustTime(t, "2026-03-02T00:00:00Z"),
					},
				}
			},
			wantQuery: []string{
				"endTimeLt=2026-03-02T00:00:00Z",
				"pageNumber=0",
				"pageSize=3",
				"startTimeGt=2026-03-01T00:00:00Z",
			},
		},
		{
			name: "every filter set at once",
			opts: func(t *testing.T) sddclcm.ListTasksOptions {
				return sddclcm.ListTasksOptions{
					PageSize: 3,
					Filter: sddclcm.TaskFilter{
						Status:             "SUCCEEDED",
						Type:               "apply",
						CreatedBy:          "admin",
						Name:               "vcfa_90_to_91_upgrade",
						Description:        "Started upgrade",
						ResourceID:         "af6ef462-e192-4fe1-9522-67a50a2b3392",
						ResourceType:       "COMPONENT",
						StartTimeGt:        mustTime(t, "2026-03-01T00:00:00Z"),
						StartTimeLt:        mustTime(t, "2026-03-02T00:00:00Z"),
						UpdateTimeGt:       mustTime(t, "2026-03-03T00:00:00Z"),
						UpdateTimeLt:       mustTime(t, "2026-03-04T00:00:00Z"),
						EndTimeGt:          mustTime(t, "2026-03-05T00:00:00Z"),
						EndTimeLt:          mustTime(t, "2026-03-06T00:00:00Z"),
						IncludeSystemTasks: ptrBool(true),
					},
				}
			},
			wantQuery: []string{
				"createdBy=admin",
				"description=Started upgrade",
				"endTimeGt=2026-03-05T00:00:00Z",
				"endTimeLt=2026-03-06T00:00:00Z",
				"includeSystemTasks=true",
				"name=vcfa_90_to_91_upgrade",
				"pageNumber=0",
				"pageSize=3",
				"resourceId=af6ef462-e192-4fe1-9522-67a50a2b3392",
				"resourceType=COMPONENT",
				"startTimeGt=2026-03-01T00:00:00Z",
				"startTimeLt=2026-03-02T00:00:00Z",
				"status=SUCCEEDED",
				"type=apply",
				"updateTimeGt=2026-03-03T00:00:00Z",
				"updateTimeLt=2026-03-04T00:00:00Z",
			},
		},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := mockvcf.New(t, contractPath(t), mockvcf.Config{
				Token: testToken,
				Pages: fixturePages(),
			})
			client := newClient(t, srv)

			if _, err := client.ListAllTasks(context.Background(), tc.opts(t)); err != nil {
				t.Fatalf("ListAllTasks: %v", err)
			}

			requests := srv.RequestsFor("getTasks")
			if len(requests) == 0 {
				t.Fatal("no getTasks request reached the mock")
			}
			var first mockvcf.Request
			found := false
			for _, r := range requests {
				if r.PageNumber() == 0 {
					first, found = r, true
					break
				}
			}
			if !found {
				t.Fatalf("no request for page 0; requests: %+v", requests)
			}

			if first.Method != http.MethodGet {
				t.Errorf("method = %q, want GET", first.Method)
			}
			if want := "/sddc-lcm/v1/tasks"; first.Path != want {
				t.Errorf("path = %q, want %q", first.Path, want)
			}
			if want := "Bearer " + testToken; first.Authz != want {
				t.Errorf("Authorization = %q, want %q", first.Authz, want)
			}
			if first.Accept != "application/json" {
				t.Errorf("Accept = %q, want application/json", first.Accept)
			}
			if first.BodyLen != 0 {
				t.Errorf("GET carried a %d byte body, want none", first.BodyLen)
			}
			if got := first.SortedQuery(); !reflect.DeepEqual(got, tc.wantQuery) {
				t.Errorf("query =\n  %v\nwant\n  %v\n(raw %q)", got, tc.wantQuery, first.RawQuery)
			}

			// Every page request must carry the same filter surface, differing
			// only in pageNumber.
			for _, r := range requests {
				for key, values := range r.Query {
					for _, v := range values {
						if v == "" {
							t.Errorf("page %d sent %q with an empty value (raw %q)",
								r.PageNumber(), key, r.RawQuery)
						}
					}
				}
			}
		})
	}
}

// TestGetComponentsRequestWireShape covers the Fleet/instance component scope,
// which is the one optional parameter of getComponents.
func TestGetComponentsRequestWireShape(t *testing.T) {
	t.Parallel()

	components := []map[string]any{
		{"id": "c-1", "componentType": "VCF_OPERATIONS", "scope": "FLEET", "version": "9.1.0.0"},
		{"id": "c-2", "componentType": "VCENTER", "scope": "INSTANCE", "version": "9.1.0.0"},
	}

	cases := []struct {
		name      string
		scope     string
		wantQuery []string
		wantIDs   []string
	}{
		{
			name:      "unset scope is omitted entirely",
			scope:     "",
			wantQuery: []string{},
			wantIDs:   []string{"c-1", "c-2"},
		},
		{
			name:      "fleet scope",
			scope:     sddclcm.ScopeFleet,
			wantQuery: []string{"scope=FLEET"},
			wantIDs:   []string{"c-1"},
		},
		{
			name:      "instance scope",
			scope:     sddclcm.ScopeInstance,
			wantQuery: []string{"scope=INSTANCE"},
			wantIDs:   []string{"c-2"},
		},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := mockvcf.New(t, contractPath(t), mockvcf.Config{
				Token:      testToken,
				Components: components,
			})
			client := newClient(t, srv)

			got, err := client.ListComponents(context.Background(), tc.scope)
			if err != nil {
				t.Fatalf("ListComponents(%q): %v", tc.scope, err)
			}

			gotIDs := make([]string, len(got))
			for i, c := range got {
				gotIDs[i] = c.ID
			}
			if !reflect.DeepEqual(gotIDs, tc.wantIDs) {
				t.Errorf("component ids = %v, want %v", gotIDs, tc.wantIDs)
			}

			requests := srv.RequestsFor("getComponents")
			if len(requests) != 1 {
				t.Fatalf("got %d getComponents requests, want 1", len(requests))
			}
			r := requests[0]
			if want := "/sddc-lcm/v1/components"; r.Path != want {
				t.Errorf("path = %q, want %q", r.Path, want)
			}
			if got := r.SortedQuery(); !reflect.DeepEqual(got, tc.wantQuery) {
				t.Errorf("query = %v, want %v (raw %q)", got, tc.wantQuery, r.RawQuery)
			}
			if tc.scope == "" && r.RawQuery != "" {
				t.Errorf("unset scope produced a query string %q, want none", r.RawQuery)
			}
		})
	}
}

// TestListAllTasksRetrievesEveryPage checks that the traversal is complete and
// that each page is requested exactly once.
func TestListAllTasksRetrievesEveryPage(t *testing.T) {
	t.Parallel()

	pages := fixturePages()
	srv := mockvcf.New(t, contractPath(t), mockvcf.Config{Token: testToken, Pages: pages})
	client := newClient(t, srv)

	got, err := client.ListAllTasks(context.Background(), sddclcm.ListTasksOptions{PageSize: 3})
	if err != nil {
		t.Fatalf("ListAllTasks: %v", err)
	}
	if len(got) != len(wantOrder) {
		t.Fatalf("got %d tasks (%v), want %d after de-duplication", len(got), ids(got), len(wantOrder))
	}

	seen := map[int]int{}
	for _, r := range srv.RequestsFor("getTasks") {
		seen[r.PageNumber()]++
	}
	for page := range pages {
		if seen[page] != 1 {
			t.Errorf("page %d was requested %d times, want exactly 1 (all: %v)", page, seen[page], seen)
		}
	}
	if len(seen) != len(pages) {
		t.Errorf("requested %d distinct pages, want %d (%v)", len(seen), len(pages), seen)
	}
}

// TestListAllTasksStableOrder pins the emitted order and the de-duplication
// rule: the occurrence on the lowest page wins.
func TestListAllTasksStableOrder(t *testing.T) {
	t.Parallel()

	t.Run("lowest index wins within one page", func(t *testing.T) {
		srv := mockvcf.New(t, contractPath(t), mockvcf.Config{
			Token: testToken,
			Pages: [][]map[string]any{{
				task("same-id", "2026-03-01T09:00:00Z", "SUCCEEDED"),
				task("same-id", "2026-03-01T08:00:00Z", "FAILED"),
			}},
		})
		client := newClient(t, srv)

		got, err := client.ListAllTasks(context.Background(), sddclcm.ListTasksOptions{})
		if err != nil {
			t.Fatalf("ListAllTasks: %v", err)
		}
		if len(got) != 1 || got[0].ID != "same-id" || got[0].Status != "SUCCEEDED" {
			t.Fatalf("duplicate resolution = %+v, want the first occurrence", got)
		}
	})

	cases := []struct {
		name         string
		concurrency  int
		pageSize     int
		wantPageSize int
	}{
		{name: "default options", concurrency: 0, pageSize: 0, wantPageSize: 50},
		{name: "page size below the floor is clamped", concurrency: 0, pageSize: -7, wantPageSize: 1},
		{name: "minimum concurrency", concurrency: 2, pageSize: 3, wantPageSize: 3},
		{name: "wide concurrency", concurrency: 8, pageSize: 3, wantPageSize: 3},
		{name: "page size above the ceiling is clamped", concurrency: 0, pageSize: 500, wantPageSize: 50},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := mockvcf.New(t, contractPath(t), mockvcf.Config{
				Token: testToken,
				Pages: fixturePages(),
			})
			client := newClient(t, srv)
			opts := sddclcm.ListTasksOptions{PageSize: tc.pageSize, Concurrency: tc.concurrency}

			// Repeat: a correct implementation is order-stable across runs even
			// though follower pages complete in a nondeterministic order.
			for attempt := 0; attempt < 5; attempt++ {
				got, err := client.ListAllTasks(context.Background(), opts)
				if err != nil {
					t.Fatalf("attempt %d: ListAllTasks: %v", attempt, err)
				}
				if !reflect.DeepEqual(ids(got), wantOrder) {
					t.Fatalf("attempt %d: order =\n  %v\nwant\n  %v", attempt, ids(got), wantOrder)
				}
				for _, ts := range got {
					if ts.ID == "t-03" && ts.Status != "SUCCEEDED" {
						t.Fatalf("attempt %d: duplicate t-03 resolved to status %q, want the page 0 occurrence (SUCCEEDED)",
							attempt, ts.Status)
					}
				}
			}

			for _, r := range srv.RequestsFor("getTasks") {
				size := r.Query["pageSize"]
				if len(size) != 1 {
					t.Fatalf("pageSize was not sent exactly once: %v", r.Query)
				}
				if size[0] != fmt.Sprint(tc.wantPageSize) {
					t.Errorf("pageSize = %s, want %d", size[0], tc.wantPageSize)
				}
			}
		})
	}
}

// TestFollowerPagesAreFetchedConcurrently uses a server-side barrier: pages
// after the first only complete once several of them are in flight at once.
func TestFollowerPagesAreFetchedConcurrently(t *testing.T) {
	t.Parallel()

	srv := mockvcf.New(t, contractPath(t), mockvcf.Config{
		Token:                      testToken,
		Pages:                      fixturePages(),
		RequireConcurrentFollowers: 2,
		BarrierTimeout:             3 * time.Second,
	})
	client := newClient(t, srv)

	got, err := client.ListAllTasks(context.Background(), sddclcm.ListTasksOptions{PageSize: 3})
	if err != nil {
		t.Fatalf("follower pages were not fetched concurrently: %v", err)
	}
	if !reflect.DeepEqual(ids(got), wantOrder) {
		t.Fatalf("order = %v, want %v", ids(got), wantOrder)
	}
}

// TestConcurrencyIsBounded checks that the client honours its concurrency
// bound instead of firing every follower page at once.
func TestConcurrencyIsBounded(t *testing.T) {
	t.Parallel()

	pages := make([][]map[string]any, 0, 6)
	for i := 0; i < 6; i++ {
		pages = append(pages, []map[string]any{
			task(fmt.Sprintf("p-%02d", i), fmt.Sprintf("2026-03-01T%02d:00:00Z", i), "SUCCEEDED"),
		})
	}

	cases := []struct {
		name        string
		concurrency int
		barrier     int
		wantError   bool
	}{
		{name: "requested bound of two is not exceeded", concurrency: 2, barrier: 3, wantError: true},
		{name: "one is raised to two", concurrency: 1, barrier: 2},
		{name: "one is raised only to two", concurrency: 1, barrier: 3, wantError: true},
		{name: "negative is raised to two", concurrency: -8, barrier: 2},
		{name: "default permits four", concurrency: 0, barrier: 4},
		{name: "default is bounded at four", concurrency: 0, barrier: 5, wantError: true},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			srv := mockvcf.New(t, contractPath(t), mockvcf.Config{
				Token:                      testToken,
				Pages:                      pages,
				RequireConcurrentFollowers: tc.barrier,
				BarrierTimeout:             2 * time.Second,
			})
			client := newClient(t, srv)

			got, err := client.ListAllTasks(context.Background(), sddclcm.ListTasksOptions{
				PageSize:    1,
				Concurrency: tc.concurrency,
			})
			if !tc.wantError {
				if err != nil {
					t.Fatalf("ListAllTasks: %v", err)
				}
				if len(got) != len(pages) {
					t.Fatalf("got %d tasks, want %d", len(got), len(pages))
				}
				return
			}

			if err == nil {
				t.Fatal("expected a 503 because the configured concurrency cannot open the barrier")
			}
			if got != nil {
				t.Fatalf("partial result returned alongside an error: %v", ids(got))
			}
			var apiErr *sddclcm.APIError
			if !errors.As(err, &apiErr) || apiErr.Status != http.StatusServiceUnavailable {
				t.Fatalf("error = %v, want an APIError with status 503", err)
			}
		})
	}
}

// TestPageFailurePropagates checks that a failing follower page surfaces as an
// error instead of a silently truncated collection.
func TestPageFailurePropagates(t *testing.T) {
	t.Parallel()

	srv := mockvcf.New(t, contractPath(t), mockvcf.Config{
		Token:    testToken,
		Pages:    fixturePages(),
		FailPage: map[int]int{2: http.StatusInternalServerError},
	})
	client := newClient(t, srv)

	got, err := client.ListAllTasks(context.Background(), sddclcm.ListTasksOptions{PageSize: 3})
	if err == nil {
		t.Fatalf("expected an error, got %d tasks", len(got))
	}
	if got != nil {
		t.Errorf("partial result returned alongside an error: %v", ids(got))
	}
	var apiErr *sddclcm.APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("error = %v, want an *APIError", err)
	}
	if apiErr.OperationID != "getTasks" || apiErr.Status != http.StatusInternalServerError {
		t.Errorf("APIError = %+v, want getTasks / 500", apiErr)
	}
}

// TestSinglePageCollection covers the degenerate cases: one page, and none.
func TestSinglePageCollection(t *testing.T) {
	t.Parallel()

	cases := []struct {
		name    string
		pages   [][]map[string]any
		wantIDs []string
		wantReq int
	}{
		{
			name:    "one page issues no follower request",
			pages:   [][]map[string]any{{task("t-01", "2026-03-01T09:00:00Z", "RUNNING")}},
			wantIDs: []string{"t-01"},
			wantReq: 1,
		},
		{
			name:    "empty collection",
			pages:   [][]map[string]any{{}},
			wantIDs: []string{},
			wantReq: 1,
		},
	}

	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			srv := mockvcf.New(t, contractPath(t), mockvcf.Config{Token: testToken, Pages: tc.pages})
			client := newClient(t, srv)

			got, err := client.ListAllTasks(context.Background(), sddclcm.ListTasksOptions{PageSize: 3})
			if err != nil {
				t.Fatalf("ListAllTasks: %v", err)
			}
			if got == nil {
				t.Fatal("returned a nil slice, want an empty non-nil slice")
			}
			if !reflect.DeepEqual(ids(got), tc.wantIDs) {
				t.Errorf("ids = %v, want %v", ids(got), tc.wantIDs)
			}
			if n := len(srv.RequestsFor("getTasks")); n != tc.wantReq {
				t.Errorf("%d getTasks requests, want %d", n, tc.wantReq)
			}
		})
	}
}

// TestConcurrentCallersShareTheClient exercises the client under -race.
func TestConcurrentCallersShareTheClient(t *testing.T) {
	t.Parallel()

	srv := mockvcf.New(t, contractPath(t), mockvcf.Config{Token: testToken, Pages: fixturePages()})
	client := newClient(t, srv)

	var wg sync.WaitGroup
	results := make([][]string, 8)
	errs := make([]error, 8)
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			got, err := client.ListAllTasks(context.Background(), sddclcm.ListTasksOptions{
				PageSize: 3,
				Filter:   sddclcm.TaskFilter{Status: "SUCCEEDED"},
			})
			results[i], errs[i] = ids(got), err
		}(i)
	}
	wg.Wait()

	for i := range results {
		if errs[i] != nil {
			t.Fatalf("caller %d: %v", i, errs[i])
		}
		if !reflect.DeepEqual(results[i], wantOrder) {
			t.Errorf("caller %d: order = %v, want %v", i, results[i], wantOrder)
		}
	}
	for _, r := range srv.RequestsFor("getTasks") {
		if got := r.Query["status"]; len(got) != 1 || got[0] != "SUCCEEDED" {
			t.Errorf("concurrent callers corrupted the filter: status=%v", got)
		}
	}
}

// TestMockServesOnlyContractOperations proves the fixture is pinned to the
// contract: an SDDC LCM operation the contract does not name is refused.
func TestMockServesOnlyContractOperations(t *testing.T) {
	t.Parallel()

	contract := mockvcf.LoadContract(t, contractPath(t))
	named := map[string]bool{}
	for _, op := range contract.Operations {
		named[op.Path] = true
	}
	for _, path := range []string{"/v1/tasks", "/v1/components"} {
		if !named[path] {
			t.Fatalf("contract no longer names %s", path)
		}
	}

	srv := mockvcf.New(t, contractPath(t), mockvcf.Config{Token: testToken, Pages: fixturePages()})
	for _, path := range []string{"/v1/health", "/v1/config", "/v1/nodes", "/v1/components/backups"} {
		resp, err := http.Get(srv.BaseURL() + path)
		if err != nil {
			t.Fatalf("GET %s: %v", path, err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusNotFound {
			t.Errorf("GET %s = %d, want 404: the mock must serve only the contract operations",
				path, resp.StatusCode)
		}
	}

	var unmatched []string
	for _, r := range srv.Requests() {
		if r.OperationID == "" {
			unmatched = append(unmatched, r.Path)
		}
	}
	sort.Strings(unmatched)
	if len(unmatched) != 4 {
		t.Errorf("request log recorded %d unmatched requests (%v), want 4", len(unmatched), unmatched)
	}
}
