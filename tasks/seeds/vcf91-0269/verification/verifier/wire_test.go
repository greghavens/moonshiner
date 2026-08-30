// Package verifier checks opsclient against the contract in docs/contract.json
// using the loopback mock in internal/opsmock. It contacts no VMware endpoint.
package verifier

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"

	"example.com/vcfops/internal/opsmock"
	"example.com/vcfops/opsclient"
)

func goodCreds() opsclient.Credentials {
	return opsclient.Credentials{
		Username: opsmock.ValidUsername,
		Password: opsmock.ValidPassword,
	}
}

// newAuthedClient starts a mock over n definitions and returns a client that
// has already acquired a token, with the request log cleared.
func newAuthedClient(t *testing.T, n int) (*opsclient.Client, *opsmock.Server) {
	t.Helper()
	srv := opsmock.New(opsmock.ServerOrder(n))
	t.Cleanup(srv.Close)
	c := opsclient.New(srv.URL(), srv.Client())
	if err := c.AcquireToken(context.Background(), goodCreds()); err != nil {
		t.Fatalf("AcquireToken: %v", err)
	}
	srv.Reset()
	return c, srv
}

func ids(defs []opsclient.SymptomDefinition) []string {
	out := make([]string, 0, len(defs))
	for _, d := range defs {
		out = append(out, d.ID)
	}
	return out
}

func wantIDs(defs []opsmock.SymptomDefinition) []string {
	out := make([]string, 0, len(defs))
	for _, d := range defs {
		out = append(out, d.ID)
	}
	return out
}

// TestOnlyContractOperationsAreServed pins the mock to the contract: a route
// the contract does not name is not served.
func TestOnlyContractOperationsAreServed(t *testing.T) {
	srv := opsmock.New(opsmock.ServerOrder(3))
	defer srv.Close()

	cases := []struct {
		name   string
		method string
		path   string
		want   int
	}{
		{"named collection operation", http.MethodGet, "/api/symptomdefinitions", http.StatusUnauthorized},
		{"unnamed alerts operation", http.MethodGet, "/api/alerts", http.StatusNotFound},
		{"unnamed resources operation", http.MethodGet, "/api/resources", http.StatusNotFound},
		{"unnamed log management operation", http.MethodGet, "/api/logs/queryconfigs", http.StatusNotFound},
		{"wrong method on named operation", http.MethodDelete, "/api/symptomdefinitions", http.StatusMethodNotAllowed},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req, err := http.NewRequest(tc.method, srv.URL()+tc.path, nil)
			if err != nil {
				t.Fatalf("build request: %v", err)
			}
			resp, err := srv.Client().Do(req)
			if err != nil {
				t.Fatalf("do request: %v", err)
			}
			defer resp.Body.Close()
			if resp.StatusCode != tc.want {
				t.Fatalf("%s %s: status = %d, want %d", tc.method, tc.path, resp.StatusCode, tc.want)
			}
		})
	}
}

// TestAcquireTokenWireShape asserts the exact request acquireToken produces,
// including that the optional authSource property is absent when unset.
func TestAcquireTokenWireShape(t *testing.T) {
	cases := []struct {
		name         string
		creds        opsclient.Credentials
		wantBodyKeys []string
		wantErr      bool
	}{
		{
			name:         "auth source unset is omitted from the body",
			creds:        opsclient.Credentials{Username: opsmock.ValidUsername, Password: opsmock.ValidPassword},
			wantBodyKeys: []string{"password", "username"},
		},
		{
			name: "auth source set is sent",
			creds: opsclient.Credentials{
				Username:   opsmock.ValidUsername,
				Password:   opsmock.ValidPassword,
				AuthSource: opsmock.ValidAuthSource,
			},
			wantBodyKeys: []string{"authSource", "password", "username"},
		},
		{
			name:         "bad password is reported",
			creds:        opsclient.Credentials{Username: opsmock.ValidUsername, Password: "wrong"},
			wantBodyKeys: []string{"password", "username"},
			wantErr:      true,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := opsmock.New(opsmock.ServerOrder(3))
			defer srv.Close()
			c := opsclient.New(srv.URL(), srv.Client())

			err := c.AcquireToken(context.Background(), tc.creds)
			if tc.wantErr && err == nil {
				t.Fatalf("AcquireToken: want error, got nil")
			}
			if !tc.wantErr && err != nil {
				t.Fatalf("AcquireToken: %v", err)
			}

			reqs := srv.Requests()
			if len(reqs) != 1 {
				t.Fatalf("request count = %d, want 1: %s", len(reqs), summarize(reqs))
			}
			r := reqs[0]

			if r.Method != http.MethodPost {
				t.Errorf("method = %q, want POST", r.Method)
			}
			if r.Path != opsmock.PathAcquireToken {
				t.Errorf("path = %q, want %q", r.Path, opsmock.PathAcquireToken)
			}
			if r.RawQuery != "" {
				t.Errorf("raw query = %q, want empty: acquireToken declares no query parameters", r.RawQuery)
			}
			// acquireToken declares security: [] and must be unauthenticated.
			if r.AuthorizationPresent {
				t.Errorf("Authorization header present (%q); acquireToken declares security: []", r.Authorization)
			}
			if got := mediaType(r.ContentType); got != "application/json" {
				t.Errorf("Content-Type = %q, want application/json", r.ContentType)
			}
			if !acceptsJSON(r.Accept) {
				t.Errorf("Accept = %q, want it to allow application/json", r.Accept)
			}
			if !reflect.DeepEqual(r.BodyKeys, tc.wantBodyKeys) {
				t.Errorf("body keys = %v, want %v (body: %s)", r.BodyKeys, tc.wantBodyKeys, r.Body)
			}
			assertNoEmptyOrNullJSONValues(t, r.Body)

			if tc.wantErr {
				if c.Token() != "" {
					t.Errorf("token = %q after a failed acquire, want empty", c.Token())
				}
				return
			}
			if c.Token() != opsmock.IssuedToken {
				t.Errorf("token = %q, want %q", c.Token(), opsmock.IssuedToken)
			}
		})
	}
}

// TestListSymptomDefinitionsWireShape asserts that the paginated collection is
// retrieved completely, emitted in a stable order, and requested with exactly
// the parameters the caller asked for.
func TestListSymptomDefinitionsWireShape(t *testing.T) {
	cases := []struct {
		name string
		// total definitions the appliance holds
		total  int
		filter opsclient.Filter
		// keep selects the definitions the filter should match
		keep func(opsmock.SymptomDefinition) bool
		// wantQueryKeys is the exact sorted set of query parameter names every
		// collection request must carry
		wantQueryKeys []string
		// wantPages is the exact ordered sequence of page numbers requested
		wantPages []int
		wantSize  int
	}{
		{
			name:          "no filter, one full page",
			total:         5,
			filter:        opsclient.Filter{PageSize: 5},
			wantQueryKeys: []string{"page", "pageSize"},
			wantPages:     []int{0},
			wantSize:      5,
		},
		{
			name:          "no filter, three pages with a short last page",
			total:         7,
			filter:        opsclient.Filter{PageSize: 3},
			wantQueryKeys: []string{"page", "pageSize"},
			wantPages:     []int{0, 1, 2},
			wantSize:      3,
		},
		{
			name:          "no filter, three exact pages and no over-fetch",
			total:         6,
			filter:        opsclient.Filter{PageSize: 2},
			wantQueryKeys: []string{"page", "pageSize"},
			wantPages:     []int{0, 1, 2},
			wantSize:      2,
		},
		{
			name:          "page size defaults to the declared 1000",
			total:         9,
			filter:        opsclient.Filter{},
			wantQueryKeys: []string{"page", "pageSize"},
			wantPages:     []int{0},
			wantSize:      1000,
		},
		{
			name:          "empty collection needs exactly one request",
			total:         0,
			filter:        opsclient.Filter{PageSize: 4},
			wantQueryKeys: []string{"page", "pageSize"},
			wantPages:     []int{0},
			wantSize:      4,
		},
		{
			name:          "adapter kind filter",
			total:         11,
			filter:        opsclient.Filter{AdapterKind: "NSX", PageSize: 2},
			keep:          func(d opsmock.SymptomDefinition) bool { return d.AdapterKindKey == "NSX" },
			wantQueryKeys: []string{"adapterKind", "page", "pageSize"},
			wantPages:     []int{0, 1},
			wantSize:      2,
		},
		{
			name:   "adapter and resource kind filters",
			total:  12,
			filter: opsclient.Filter{AdapterKind: "VMWARE", ResourceKind: "HostSystem", PageSize: 3},
			keep: func(d opsmock.SymptomDefinition) bool {
				return d.AdapterKindKey == "VMWARE" && d.ResourceKindKey == "HostSystem"
			},
			wantQueryKeys: []string{"adapterKind", "page", "pageSize", "resourceKind"},
			wantPages:     []int{0, 1},
			wantSize:      3,
		},
		{
			name:   "case-insensitive name substring filter",
			total:  12,
			filter: opsclient.Filter{Name: "memory usage", PageSize: 2},
			keep: func(d opsmock.SymptomDefinition) bool {
				return strings.Contains(strings.ToLower(d.Name), "memory usage")
			},
			wantQueryKeys: []string{"name", "page", "pageSize"},
			wantPages:     []int{0, 1},
			wantSize:      2,
		},
		{
			name:   "repeated id array parameter",
			total:  9,
			filter: opsclient.Filter{IDs: []string{"SymptomDefinition-07", "SymptomDefinition-02"}, PageSize: 1},
			keep: func(d opsmock.SymptomDefinition) bool {
				return d.ID == "SymptomDefinition-07" || d.ID == "SymptomDefinition-02"
			},
			wantQueryKeys: []string{"id", "page", "pageSize"},
			wantPages:     []int{0, 1},
			wantSize:      1,
		},
		{
			name:   "every filter set at once",
			total:  12,
			filter: opsclient.Filter{AdapterKind: "VMWARE", ResourceKind: "VirtualMachine", Name: "cpu", IDs: []string{"SymptomDefinition-01", "SymptomDefinition-04", "SymptomDefinition-10"}, PageSize: 2},
			keep: func(d opsmock.SymptomDefinition) bool {
				switch d.ID {
				case "SymptomDefinition-01", "SymptomDefinition-04", "SymptomDefinition-10":
				default:
					return false
				}
				return d.AdapterKindKey == "VMWARE" && d.ResourceKindKey == "VirtualMachine" &&
					strings.Contains(strings.ToLower(d.Name), "cpu")
			},
			wantQueryKeys: []string{"adapterKind", "id", "name", "page", "pageSize", "resourceKind"},
			wantPages:     []int{0, 1},
			wantSize:      2,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c, srv := newAuthedClient(t, tc.total)

			got, err := c.ListSymptomDefinitions(context.Background(), tc.filter)
			if err != nil {
				t.Fatalf("ListSymptomDefinitions: %v", err)
			}

			want := opsmock.Expect(tc.total, tc.keep)
			if !reflect.DeepEqual(ids(got), wantIDs(want)) {
				t.Fatalf("ids = %v\nwant  %v", ids(got), wantIDs(want))
			}
			if !reflect.DeepEqual(toMock(got), want) {
				t.Fatalf("entries = %#v\nwant      %#v", toMock(got), want)
			}
			assertSortedByID(t, got)

			reqs := srv.Requests()
			if len(reqs) != len(tc.wantPages) {
				t.Fatalf("collection request count = %d, want %d: %s",
					len(reqs), len(tc.wantPages), summarize(reqs))
			}
			var gotPages []int
			for _, r := range reqs {
				assertCollectionRequest(t, r, tc.wantQueryKeys, tc.wantSize, tc.filter)
				gotPages = append(gotPages, intQuery(t, r, "page"))
			}
			if !reflect.DeepEqual(gotPages, tc.wantPages) {
				t.Fatalf("page sequence = %v, want %v", gotPages, tc.wantPages)
			}
		})
	}
}

// TestListSymptomDefinitionsIsConcurrencySafe runs overlapping retrievals
// against one client. Under -race this also catches shared mutable state.
func TestListSymptomDefinitionsIsConcurrencySafe(t *testing.T) {
	const (
		total      = 11
		pageSize   = 2
		goroutines = 8
	)
	c, srv := newAuthedClient(t, total)

	want := opsmock.Expect(total, nil)
	wantPagesPerCall := (total + pageSize - 1) / pageSize

	results := make([][]opsclient.SymptomDefinition, goroutines)
	errs := make([]error, goroutines)
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := 0; i < goroutines; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			results[i], errs[i] = c.ListSymptomDefinitions(
				context.Background(), opsclient.Filter{PageSize: pageSize})
		}(i)
	}
	close(start)
	wg.Wait()

	for i := 0; i < goroutines; i++ {
		if errs[i] != nil {
			t.Fatalf("goroutine %d: %v", i, errs[i])
		}
		if !reflect.DeepEqual(toMock(results[i]), want) {
			t.Fatalf("goroutine %d: ids = %v\nwant %v", i, ids(results[i]), wantIDs(want))
		}
	}
	// Each caller must get its own slice; concurrent callers must not share
	// backing storage.
	for i := 1; i < goroutines; i++ {
		if len(results[i]) > 0 && &results[i][0] == &results[0][0] {
			t.Fatalf("goroutines 0 and %d returned the same backing array", i)
		}
	}

	reqs := srv.Requests()
	if want := goroutines * wantPagesPerCall; len(reqs) != want {
		t.Fatalf("collection request count = %d, want %d: %s", len(reqs), want, summarize(reqs))
	}
	pageCounts := map[int]int{}
	for _, r := range reqs {
		assertCollectionRequest(t, r, []string{"page", "pageSize"}, pageSize, opsclient.Filter{PageSize: pageSize})
		pageCounts[intQuery(t, r, "page")]++
	}
	for p := 0; p < wantPagesPerCall; p++ {
		if pageCounts[p] != goroutines {
			t.Errorf("page %d requested %d times, want %d", p, pageCounts[p], goroutines)
		}
	}
	if len(pageCounts) != wantPagesPerCall {
		t.Errorf("distinct pages requested = %d, want %d", len(pageCounts), wantPagesPerCall)
	}
}

// TestListRequiresToken asserts the client refuses to call an authenticated
// operation before acquireToken has succeeded.
func TestListRequiresToken(t *testing.T) {
	srv := opsmock.New(opsmock.ServerOrder(3))
	defer srv.Close()
	c := opsclient.New(srv.URL(), srv.Client())

	if _, err := c.ListSymptomDefinitions(context.Background(), opsclient.Filter{PageSize: 2}); err == nil {
		t.Fatalf("ListSymptomDefinitions without a token: want error, got nil")
	}
	if reqs := srv.Requests(); len(reqs) != 0 {
		t.Fatalf("request count = %d, want 0: %s", len(reqs), summarize(reqs))
	}
}

// assertCollectionRequest checks one getSymptomDefinitions request against the
// contract.
func assertCollectionRequest(t *testing.T, r opsmock.Request, wantKeys []string, wantPageSize int, f opsclient.Filter) {
	t.Helper()

	if r.Method != http.MethodGet {
		t.Errorf("seq %d: method = %q, want GET", r.Seq, r.Method)
	}
	if r.Path != opsmock.PathSymptomDefinitions {
		t.Errorf("seq %d: path = %q, want %q", r.Seq, r.Path, opsmock.PathSymptomDefinitions)
	}
	if len(r.Body) != 0 {
		t.Errorf("seq %d: GET carried a body: %s", r.Seq, r.Body)
	}
	if !r.AuthorizationPresent {
		t.Errorf("seq %d: missing Authorization header", r.Seq)
	} else if want := opsmock.TokenPrefix + opsmock.IssuedToken; r.Authorization != want {
		t.Errorf("seq %d: Authorization = %q, want %q", r.Seq, r.Authorization, want)
	}
	if !acceptsJSON(r.Accept) {
		t.Errorf("seq %d: Accept = %q, want it to allow application/json", r.Seq, r.Accept)
	}

	if !reflect.DeepEqual(r.QueryKeys, wantKeys) {
		t.Errorf("seq %d: query keys = %v, want %v (raw query: %q)", r.Seq, r.QueryKeys, wantKeys, r.RawQuery)
	}
	// An optional parameter left unset must be absent, not present and empty.
	for _, pair := range strings.Split(r.RawQuery, "&") {
		if pair == "" {
			continue
		}
		if !strings.Contains(pair, "=") || strings.HasSuffix(pair, "=") {
			t.Errorf("seq %d: query %q sends %q with an empty value; unset optional parameters must be omitted",
				r.Seq, r.RawQuery, pair)
		}
	}
	if got := r.Query["pageSize"]; len(got) != 1 || got[0] != strconv.Itoa(wantPageSize) {
		t.Errorf("seq %d: pageSize = %v, want [%d]", r.Seq, got, wantPageSize)
	}
	// Repeated array parameter values must be preserved in caller order.
	if len(f.IDs) > 0 && !reflect.DeepEqual(r.Query["id"], f.IDs) {
		t.Errorf("seq %d: id = %v, want %v", r.Seq, r.Query["id"], f.IDs)
	}
	for name, want := range map[string]string{
		"adapterKind":  f.AdapterKind,
		"resourceKind": f.ResourceKind,
		"name":         f.Name,
	} {
		if want == "" {
			continue
		}
		if got := r.Query[name]; len(got) != 1 || got[0] != want {
			t.Errorf("seq %d: %s = %v, want [%s]", r.Seq, name, got, want)
		}
	}
}

func assertSortedByID(t *testing.T, defs []opsclient.SymptomDefinition) {
	t.Helper()
	if !sort.SliceIsSorted(defs, func(a, b int) bool { return defs[a].ID < defs[b].ID }) {
		t.Errorf("result is not sorted ascending by id: %v", ids(defs))
	}
}

// assertNoEmptyOrNullJSONValues rejects a body that encodes an unset optional
// property as null or as an empty string.
func assertNoEmptyOrNullJSONValues(t *testing.T, body []byte) {
	t.Helper()
	if len(body) == 0 {
		return
	}
	var obj map[string]any
	if err := json.Unmarshal(body, &obj); err != nil {
		t.Errorf("body is not a JSON object: %s", body)
		return
	}
	for k, v := range obj {
		if v == nil {
			t.Errorf("body property %q is null; unset optional properties must be omitted (body: %s)", k, body)
		}
		if s, ok := v.(string); ok && s == "" {
			t.Errorf("body property %q is an empty string; unset optional properties must be omitted (body: %s)", k, body)
		}
	}
}

func intQuery(t *testing.T, r opsmock.Request, name string) int {
	t.Helper()
	vals := r.Query[name]
	if len(vals) != 1 {
		t.Fatalf("seq %d: %s = %v, want exactly one value", r.Seq, name, vals)
	}
	n, err := strconv.Atoi(vals[0])
	if err != nil {
		t.Fatalf("seq %d: %s = %q, want an integer", r.Seq, name, vals[0])
	}
	return n
}

func toMock(defs []opsclient.SymptomDefinition) []opsmock.SymptomDefinition {
	out := make([]opsmock.SymptomDefinition, 0, len(defs))
	for _, d := range defs {
		out = append(out, opsmock.SymptomDefinition{
			ID:              d.ID,
			Name:            d.Name,
			AdapterKindKey:  d.AdapterKindKey,
			ResourceKindKey: d.ResourceKindKey,
		})
	}
	return out
}

func mediaType(v string) string {
	return strings.TrimSpace(strings.SplitN(v, ";", 2)[0])
}

func acceptsJSON(accept string) bool {
	for _, part := range strings.Split(accept, ",") {
		switch mediaType(part) {
		case "application/json", "application/*", "*/*":
			return true
		}
	}
	return false
}

func summarize(reqs []opsmock.Request) string {
	var b strings.Builder
	b.WriteString("\n")
	for _, r := range reqs {
		fmt.Fprintf(&b, "  [%d] %s %s?%s -> %d %s\n", r.Seq, r.Method, r.Path, r.RawQuery, r.Status, r.Rejection)
	}
	return b.String()
}
