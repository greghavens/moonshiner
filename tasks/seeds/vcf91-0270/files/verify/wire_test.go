// Package verify holds the protected acceptance checks for the customgroup
// client. It exercises the client against the loopback mock in internal/mock
// and asserts the exact bytes that go over the wire. No live VMware endpoint is
// contacted: every request in this package terminates at 127.0.0.1.
package verify

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"sync"
	"testing"

	"vcfops/customgroup"
	"vcfops/internal/contract"
	"vcfops/internal/mock"
)

const (
	groupName    = "Seed Overcommitted Clusters"
	adapterKind  = "Container"
	resourceKind = "Environment"
)

func boolPtr(b bool) *bool { return &b }

// newClient wires a client to a freshly started loopback mock.
func newClient(t *testing.T) (*customgroup.Client, *mock.Server) {
	t.Helper()
	srv := mock.New(t)
	c, err := customgroup.NewClient(srv.URL(), srv.Authorization(), srv.Client())
	if err != nil {
		t.Fatalf("NewClient: %v", err)
	}
	if c == nil {
		t.Fatal("NewClient returned a nil client and no error")
	}
	return c, srv
}

func desiredGroup() customgroup.CustomGroup {
	return customgroup.CustomGroup{
		ResourceKey: customgroup.ResourceKey{
			Name:            groupName,
			AdapterKindKey:  adapterKind,
			ResourceKindKey: resourceKind,
		},
	}
}

// ---------------------------------------------------------------------------
// Provenance
// ---------------------------------------------------------------------------

type officialSources struct {
	Sources []struct {
		Kind         string   `json:"kind"`
		Repository   string   `json:"repository"`
		SpecPath     string   `json:"specPath"`
		Commit       string   `json:"commit"`
		Permalink    string   `json:"permalink"`
		SpecSha256   string   `json:"specSha256"`
		License      string   `json:"license"`
		APIVersion   string   `json:"apiVersion"`
		OperationIDs []string `json:"operationIds"`
	} `json:"sources"`
}

func loadSources(t *testing.T) officialSources {
	t.Helper()
	path, err := contract.ModuleFile(filepath.Join("docs", "official_sources.json"))
	if err != nil {
		t.Fatalf("locate docs/official_sources.json: %v", err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read docs/official_sources.json: %v", err)
	}
	var s officialSources
	if err := json.Unmarshal(raw, &s); err != nil {
		t.Fatalf("parse docs/official_sources.json: %v", err)
	}
	if len(s.Sources) == 0 {
		t.Fatal("docs/official_sources.json records no sources")
	}
	return s
}

var sha40 = regexp.MustCompile(`^[0-9a-f]{40}$`)
var sha256Re = regexp.MustCompile(`^[0-9a-f]{64}$`)

// TestOfficialSourcesRecordSpecification checks that the provenance file names
// the OpenAPI specification file, the exact revision it was read at, and the
// operationIds the contract was derived for.
func TestOfficialSourcesRecordSpecification(t *testing.T) {
	src := loadSources(t).Sources[0]

	checks := []struct {
		name string
		got  string
		want string
	}{
		{"specPath", src.SpecPath, "specifications/vcf-operations/vcf-operations-openapi.json"},
		{"license", src.License, "Apache-2.0"},
		{"repository", src.Repository, "https://github.com/vmware/vcf-api-specs"},
	}
	for _, c := range checks {
		if c.got != c.want {
			t.Errorf("official_sources %s = %q, want %q", c.name, c.got, c.want)
		}
	}

	if !sha40.MatchString(src.Commit) {
		t.Errorf("official_sources commit = %q, want a 40 character commit sha", src.Commit)
	}
	if !sha256Re.MatchString(src.SpecSha256) {
		t.Errorf("official_sources specSha256 = %q, want a 64 character sha256", src.SpecSha256)
	}
	if !strings.Contains(src.Permalink, src.Commit) {
		t.Errorf("official_sources permalink %q does not pin the recorded commit %q",
			src.Permalink, src.Commit)
	}
	if !strings.HasPrefix(src.APIVersion, "9.1") {
		t.Errorf("official_sources apiVersion = %q, want a 9.1 API version", src.APIVersion)
	}
	if len(src.OperationIDs) == 0 {
		t.Fatal("official_sources records no operationIds")
	}
}

// TestContractMatchesOfficialSources checks that the contract and the
// provenance record describe the same revision and the same operations.
func TestContractMatchesOfficialSources(t *testing.T) {
	c := contract.MustLoad()
	src := loadSources(t).Sources[0]

	if c.Source.Path != src.SpecPath {
		t.Errorf("contract source path %q != official_sources specPath %q",
			c.Source.Path, src.SpecPath)
	}
	if c.Source.Commit != src.Commit {
		t.Errorf("contract commit %q != official_sources commit %q", c.Source.Commit, src.Commit)
	}
	if c.Source.SpecSha256 != src.SpecSha256 {
		t.Errorf("contract specSha256 %q != official_sources specSha256 %q",
			c.Source.SpecSha256, src.SpecSha256)
	}

	fromContract := c.SortedOperationIDs()
	fromSources := append([]string(nil), src.OperationIDs...)
	sort.Strings(fromSources)
	if !reflect.DeepEqual(fromContract, fromSources) {
		t.Errorf("contract operationIds %v != official_sources operationIds %v",
			fromContract, fromSources)
	}

	// The scenario needs a lookup and a create; both must be named explicitly.
	for _, want := range []string{"getCustomGroups", "createCustomGroup"} {
		op, ok := c.Operation(want)
		if !ok {
			t.Fatalf("contract does not name operationId %q", want)
			continue
		}
		if op.Path != "/api/resources/groups" {
			t.Errorf("operation %s path = %q, want /api/resources/groups", want, op.Path)
		}
	}
	if got := c.BasePath; got != "/suite-api" {
		t.Errorf("contract basePath = %q, want /suite-api", got)
	}
	if got := c.Security.HeaderName; got != "Authorization" {
		t.Errorf("contract security headerName = %q, want Authorization", got)
	}
}

// ---------------------------------------------------------------------------
// The mock serves the contract and nothing else
// ---------------------------------------------------------------------------

// TestMockIsLoopbackOnly confirms the stand-in appliance is bound to a loopback
// address, so no test in this package can reach a live VMware endpoint.
func TestMockIsLoopbackOnly(t *testing.T) {
	srv := mock.New(t)
	u, err := url.Parse(srv.URL())
	if err != nil {
		t.Fatalf("parse mock URL: %v", err)
	}
	host, _, err := net.SplitHostPort(u.Host)
	if err != nil {
		t.Fatalf("split mock host: %v", err)
	}
	ip := net.ParseIP(host)
	if ip == nil || !ip.IsLoopback() {
		t.Fatalf("mock listens on %q, want a loopback address", u.Host)
	}
}

// TestMockServesOnlyContractOperations checks that the mock is pinned to the
// contract: the two named operations route, and other real VCF Operations
// endpoints are refused and recorded as unmatched.
func TestMockServesOnlyContractOperations(t *testing.T) {
	srv := mock.New(t)
	hc := srv.Client()

	cases := []struct {
		name       string
		method     string
		path       string
		wantServed bool
		wantOpID   string
	}{
		{"list is on the contract", http.MethodGet, "/suite-api/api/resources/groups", true, "getCustomGroups"},
		{"create is on the contract", http.MethodPost, "/suite-api/api/resources/groups", true, "createCustomGroup"},
		{"group types is not", http.MethodGet, "/suite-api/api/resources/groups/types", false, ""},
		{"delete group is not", http.MethodDelete, "/suite-api/api/resources/groups/abc", false, ""},
		{"resources root is not", http.MethodGet, "/suite-api/api/resources", false, ""},
		{"log management is not", http.MethodGet, "/suite-api/api/alerts", false, ""},
		{"unprefixed path is not", http.MethodGet, "/api/resources/groups", false, ""},
		{"wrong verb is not", http.MethodPatch, "/suite-api/api/resources/groups", false, ""},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv.Reset()
			req, err := http.NewRequest(tc.method, srv.URL()+tc.path, strings.NewReader("{}"))
			if err != nil {
				t.Fatalf("build request: %v", err)
			}
			req.Header.Set("Authorization", srv.Authorization())
			req.Header.Set("Content-Type", "application/json")
			resp, err := hc.Do(req)
			if err != nil {
				t.Fatalf("do request: %v", err)
			}
			resp.Body.Close()

			served := resp.StatusCode != http.StatusNotFound
			if served != tc.wantServed {
				t.Fatalf("%s %s: status %d, served=%v want served=%v",
					tc.method, tc.path, resp.StatusCode, served, tc.wantServed)
			}

			log := srv.Requests()
			if len(log) != 1 {
				t.Fatalf("expected exactly 1 recorded request, got %d", len(log))
			}
			if log[0].OperationID != tc.wantOpID {
				t.Errorf("recorded operationId = %q, want %q", log[0].OperationID, tc.wantOpID)
			}
		})
	}
}

// TestMockRequiresContractCredential checks the security scheme is enforced.
func TestMockRequiresContractCredential(t *testing.T) {
	srv := mock.New(t)
	resp, err := srv.Client().Get(srv.URL() + "/suite-api/api/resources/groups")
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("unauthenticated list returned %d, want 401", resp.StatusCode)
	}
}

// ---------------------------------------------------------------------------
// Request wire shape
// ---------------------------------------------------------------------------

// TestCreateRequestWireShape asserts the exact JSON object createCustomGroup
// receives. Unset optional properties must be absent from the request rather
// than sent as empty strings, empty arrays or a false boolean.
func TestCreateRequestWireShape(t *testing.T) {
	cases := []struct {
		name string
		in   customgroup.CustomGroup
		want map[string]any
		// mustBeAbsent names properties that must not appear anywhere in the
		// request body. Checked separately from the exact comparison so a
		// failure points straight at the leaked property.
		mustBeAbsent []string
	}{
		{
			name: "minimal group omits every unset optional property",
			in:   desiredGroup(),
			want: map[string]any{
				"resourceKey": map[string]any{
					"name":            groupName,
					"adapterKindKey":  adapterKind,
					"resourceKindKey": resourceKind,
				},
				"membershipDefinition": map[string]any{},
			},
			mustBeAbsent: []string{
				"id", "policy", "autoResolveMembership", "links",
				"membershipDefinition.includedResources",
				"membershipDefinition.excludedResources",
				"resourceKey.resourceIdentifiers",
				"resourceKey.extension",
				"resourceKey.links",
			},
		},
		{
			name: "autoResolveMembership false is sent explicitly",
			in: func() customgroup.CustomGroup {
				g := desiredGroup()
				g.AutoResolveMembership = boolPtr(false)
				return g
			}(),
			want: map[string]any{
				"resourceKey": map[string]any{
					"name":            groupName,
					"adapterKindKey":  adapterKind,
					"resourceKindKey": resourceKind,
				},
				"autoResolveMembership": false,
				"membershipDefinition":  map[string]any{},
			},
			mustBeAbsent: []string{"id", "policy"},
		},
		{
			name: "autoResolveMembership true is sent explicitly",
			in: func() customgroup.CustomGroup {
				g := desiredGroup()
				g.AutoResolveMembership = boolPtr(true)
				return g
			}(),
			want: map[string]any{
				"resourceKey": map[string]any{
					"name":            groupName,
					"adapterKindKey":  adapterKind,
					"resourceKindKey": resourceKind,
				},
				"autoResolveMembership": true,
				"membershipDefinition":  map[string]any{},
			},
			mustBeAbsent: []string{"id", "policy"},
		},
		{
			name: "named members are carried, the unused list is omitted",
			in: func() customgroup.CustomGroup {
				g := desiredGroup()
				g.MembershipDefinition.IncludedResources = []string{
					"11111111-1111-4111-8111-111111111111",
					"22222222-2222-4222-8222-222222222222",
				}
				return g
			}(),
			want: map[string]any{
				"resourceKey": map[string]any{
					"name":            groupName,
					"adapterKindKey":  adapterKind,
					"resourceKindKey": resourceKind,
				},
				"membershipDefinition": map[string]any{
					"includedResources": []any{
						"11111111-1111-4111-8111-111111111111",
						"22222222-2222-4222-8222-222222222222",
					},
				},
			},
			mustBeAbsent: []string{"membershipDefinition.excludedResources"},
		},
		{
			name: "excluded members are carried, the unused list is omitted",
			in: func() customgroup.CustomGroup {
				g := desiredGroup()
				g.MembershipDefinition.ExcludedResources = []string{
					"33333333-3333-4333-8333-333333333333",
					"44444444-4444-4444-8444-444444444444",
				}
				return g
			}(),
			want: map[string]any{
				"resourceKey": map[string]any{
					"name":            groupName,
					"adapterKindKey":  adapterKind,
					"resourceKindKey": resourceKind,
				},
				"membershipDefinition": map[string]any{
					"excludedResources": []any{
						"33333333-3333-4333-8333-333333333333",
						"44444444-4444-4444-8444-444444444444",
					},
				},
			},
			mustBeAbsent: []string{"membershipDefinition.includedResources"},
		},
		{
			name: "server assigned properties never travel outbound",
			in: func() customgroup.CustomGroup {
				g := desiredGroup()
				g.ID = "33333333-3333-4333-8333-333333333333"
				g.Policy = "44444444-4444-4444-8444-444444444444"
				return g
			}(),
			want: map[string]any{
				"resourceKey": map[string]any{
					"name":            groupName,
					"adapterKindKey":  adapterKind,
					"resourceKindKey": resourceKind,
				},
				"membershipDefinition": map[string]any{},
			},
			mustBeAbsent: []string{"id", "policy"},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c, srv := newClient(t)
			if _, err := c.CreateCustomGroup(context.Background(), tc.in); err != nil {
				t.Fatalf("CreateCustomGroup: %v", err)
			}

			reqs := srv.RequestsFor("createCustomGroup")
			if len(reqs) != 1 {
				t.Fatalf("expected 1 createCustomGroup request, got %d", len(reqs))
			}
			got, err := reqs[0].BodyMap()
			if err != nil {
				t.Fatalf("decode request body: %v", err)
			}

			for _, path := range tc.mustBeAbsent {
				if v, present := lookup(got, path); present {
					t.Errorf("property %q must be omitted when unset, but the request sent %#v",
						path, v)
				}
			}
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("request body mismatch\n got: %s\nwant: %s", pretty(got), pretty(tc.want))
			}
		})
	}
}

// TestCreateRequestTransport asserts the non-body parts of a create request.
func TestCreateRequestTransport(t *testing.T) {
	c, srv := newClient(t)
	if _, err := c.CreateCustomGroup(context.Background(), desiredGroup()); err != nil {
		t.Fatalf("CreateCustomGroup: %v", err)
	}
	reqs := srv.RequestsFor("createCustomGroup")
	if len(reqs) != 1 {
		t.Fatalf("expected 1 createCustomGroup request, got %d", len(reqs))
	}
	r := reqs[0]

	if r.Method != http.MethodPost {
		t.Errorf("method = %s, want POST", r.Method)
	}
	if r.Path != "/suite-api/api/resources/groups" {
		t.Errorf("path = %q, want /suite-api/api/resources/groups", r.Path)
	}
	if r.RawQuery != "" {
		t.Errorf("create sent query %q, want none", r.RawQuery)
	}
	if got := r.Header.Get("Authorization"); got != srv.Authorization() {
		t.Errorf("Authorization = %q, want %q", got, srv.Authorization())
	}
	if got := r.Header.Get("Content-Type"); !strings.HasPrefix(got, "application/json") {
		t.Errorf("Content-Type = %q, want application/json", got)
	}
	if got := r.Header.Get("Accept"); !strings.Contains(got, "application/json") {
		t.Errorf("Accept = %q, want it to include application/json", got)
	}
}

// TestListRequestQueryShape asserts that optional query parameters are only
// sent when the caller asked for them.
func TestListRequestQueryShape(t *testing.T) {
	cases := []struct {
		name         string
		opts         customgroup.ListOptions
		wantRawEmpty bool
		wantValues   url.Values
	}{
		{
			name:         "zero options send no query string",
			opts:         customgroup.ListOptions{},
			wantRawEmpty: true,
			wantValues:   url.Values{},
		},
		{
			name:       "includePolicy is sent only when requested",
			opts:       customgroup.ListOptions{IncludePolicy: true},
			wantValues: url.Values{"includePolicy": {"true"}},
		},
		{
			name: "group ids repeat the parameter",
			opts: customgroup.ListOptions{GroupIDs: []string{
				"11111111-1111-4111-8111-111111111111",
				"22222222-2222-4222-8222-222222222222",
			}},
			wantValues: url.Values{"groupId": {
				"11111111-1111-4111-8111-111111111111",
				"22222222-2222-4222-8222-222222222222",
			}},
		},
		{
			name: "both parameters together",
			opts: customgroup.ListOptions{
				GroupIDs:      []string{"11111111-1111-4111-8111-111111111111"},
				IncludePolicy: true,
			},
			wantValues: url.Values{
				"groupId":       {"11111111-1111-4111-8111-111111111111"},
				"includePolicy": {"true"},
			},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c, srv := newClient(t)
			if _, err := c.ListCustomGroups(context.Background(), tc.opts); err != nil {
				t.Fatalf("ListCustomGroups: %v", err)
			}
			reqs := srv.RequestsFor("getCustomGroups")
			if len(reqs) != 1 {
				t.Fatalf("expected 1 getCustomGroups request, got %d", len(reqs))
			}
			r := reqs[0]
			if r.Method != http.MethodGet {
				t.Errorf("method = %s, want GET", r.Method)
			}
			if got := r.Header.Get("Authorization"); got != srv.Authorization() {
				t.Errorf("Authorization = %q, want %q", got, srv.Authorization())
			}
			if got := r.Header.Get("Accept"); !strings.Contains(got, "application/json") {
				t.Errorf("Accept = %q, want it to include application/json", got)
			}
			if tc.wantRawEmpty && r.RawQuery != "" {
				t.Errorf("raw query = %q, want an empty query string", r.RawQuery)
			}
			if !reflect.DeepEqual(r.Query, tc.wantValues) {
				t.Errorf("query = %v, want %v", r.Query, tc.wantValues)
			}
		})
	}
}

// TestAPIErrorCarriesOperationAndStatus checks the error contract shared by
// both operations. Authentication is deliberately rejected so the response
// reaches the client as a normal non-success status.
func TestAPIErrorCarriesOperationAndStatus(t *testing.T) {
	cases := []struct {
		name      string
		operation string
		call      func(context.Context, *customgroup.Client) error
	}{
		{
			name:      "list",
			operation: "getCustomGroups",
			call: func(ctx context.Context, c *customgroup.Client) error {
				_, err := c.ListCustomGroups(ctx, customgroup.ListOptions{})
				return err
			},
		},
		{
			name:      "create",
			operation: "createCustomGroup",
			call: func(ctx context.Context, c *customgroup.Client) error {
				_, err := c.CreateCustomGroup(ctx, desiredGroup())
				return err
			},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := mock.New(t)
			c, err := customgroup.NewClient(srv.URL(), "incorrect-token", srv.Client())
			if err != nil {
				t.Fatalf("NewClient: %v", err)
			}

			err = tc.call(context.Background(), c)
			if err == nil {
				t.Fatal("call succeeded, want authentication failure")
			}
			var apiErr *customgroup.APIError
			if !errors.As(err, &apiErr) {
				t.Fatalf("error %v is not a *customgroup.APIError", err)
			}
			if apiErr.OperationID != tc.operation {
				t.Errorf("OperationID = %q, want %q", apiErr.OperationID, tc.operation)
			}
			if apiErr.StatusCode != http.StatusUnauthorized {
				t.Errorf("StatusCode = %d, want %d",
					apiErr.StatusCode, http.StatusUnauthorized)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// Retry safety
// ---------------------------------------------------------------------------

// TestEnsureCustomGroupIsRetrySafe drives the scenarios in which a create can
// be attempted more than once. In every one of them exactly one custom group
// must exist afterwards.
func TestEnsureCustomGroupIsRetrySafe(t *testing.T) {
	cases := []struct {
		name string
		run  func(t *testing.T, c *customgroup.Client, srv *mock.Server)
	}{
		{
			name: "a second call adopts the group the first created",
			run: func(t *testing.T, c *customgroup.Client, srv *mock.Server) {
				first, err := c.EnsureCustomGroup(context.Background(), desiredGroup())
				if err != nil {
					t.Fatalf("first EnsureCustomGroup: %v", err)
				}
				if !first.Created {
					t.Error("first call reported Created=false, want true")
				}
				if first.Group.ID == "" {
					t.Error("first call returned a group with no ID")
				}

				second, err := c.EnsureCustomGroup(context.Background(), desiredGroup())
				if err != nil {
					t.Fatalf("second EnsureCustomGroup: %v", err)
				}
				if second.Created {
					t.Error("second call reported Created=true, want false")
				}
				if second.Group.ID != first.Group.ID {
					t.Errorf("second call returned ID %q, want the existing %q",
						second.Group.ID, first.Group.ID)
				}
				if n := srv.CountFor("createCustomGroup"); n != 1 {
					t.Errorf("createCustomGroup was called %d times, want 1", n)
				}
				if n := srv.CountFor("getCustomGroups"); n < 2 {
					t.Errorf("getCustomGroups was called %d times, want at least 2: "+
						"each call must look the group up before creating it", n)
				}
			},
		},
		{
			name: "a create whose response was lost is not repeated",
			run: func(t *testing.T, c *customgroup.Client, srv *mock.Server) {
				srv.FailNextCreateAfterStore()

				if _, err := c.EnsureCustomGroup(context.Background(), desiredGroup()); err == nil {
					t.Fatal("EnsureCustomGroup succeeded, want the injected 503 to surface")
				} else {
					var apiErr *customgroup.APIError
					if !errors.As(err, &apiErr) {
						t.Errorf("error %v is not a *customgroup.APIError", err)
					} else if apiErr.StatusCode != http.StatusServiceUnavailable {
						t.Errorf("APIError.StatusCode = %d, want 503", apiErr.StatusCode)
					}
				}

				retry, err := c.EnsureCustomGroup(context.Background(), desiredGroup())
				if err != nil {
					t.Fatalf("retry EnsureCustomGroup: %v", err)
				}
				if retry.Created {
					t.Error("retry reported Created=true, want false: the group already existed")
				}
				if retry.Group.ID == "" {
					t.Error("retry returned a group with no ID")
				}
				if n := srv.CountFor("createCustomGroup"); n != 1 {
					t.Errorf("createCustomGroup was called %d times, want 1: "+
						"the retry must find the group the lost response created", n)
				}
			},
		},
		{
			name: "losing the race to another writer resolves to that group",
			run: func(t *testing.T, c *customgroup.Client, srv *mock.Server) {
				srv.InsertBeforeNextCreate(groupName, adapterKind, resourceKind)

				res, err := c.EnsureCustomGroup(context.Background(), desiredGroup())
				if err != nil {
					t.Fatalf("EnsureCustomGroup: %v", err)
				}
				if res.Created {
					t.Error("reported Created=true, want false: another writer got there first")
				}
				if res.Group.ID == "" {
					t.Error("returned a group with no ID")
				}
				if n := srv.CountFor("createCustomGroup"); n != 1 {
					t.Errorf("createCustomGroup was called %d times, want 1", n)
				}
			},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c, srv := newClient(t)
			tc.run(t, c, srv)
			if names := srv.GroupNames(); len(names) != 1 || names[0] != groupName {
				t.Errorf("appliance holds %v, want exactly one group named %q", names, groupName)
			}
		})
	}
}

// TestEnsureCustomGroupUnderConcurrency runs many callers against one appliance
// and requires that exactly one of them creates the group.
func TestEnsureCustomGroupUnderConcurrency(t *testing.T) {
	const callers = 8

	c, srv := newClient(t)

	var (
		wg      sync.WaitGroup
		mu      sync.Mutex
		created int
		ids     []string
		errs    []error
	)
	wg.Add(callers)
	for i := 0; i < callers; i++ {
		go func() {
			defer wg.Done()
			res, err := c.EnsureCustomGroup(context.Background(), desiredGroup())
			mu.Lock()
			defer mu.Unlock()
			if err != nil {
				errs = append(errs, err)
				return
			}
			if res.Created {
				created++
			}
			ids = append(ids, res.Group.ID)
		}()
	}
	wg.Wait()

	for _, err := range errs {
		t.Errorf("concurrent EnsureCustomGroup failed: %v", err)
	}
	if created != 1 {
		t.Errorf("%d callers reported Created=true, want exactly 1", created)
	}
	if len(ids) != callers {
		t.Fatalf("collected %d results, want %d", len(ids), callers)
	}
	for _, id := range ids {
		if id == "" {
			t.Error("a caller returned a group with no ID")
			continue
		}
		if id != ids[0] {
			t.Errorf("callers disagree on the group ID: %q and %q", ids[0], id)
		}
	}
	if names := srv.GroupNames(); len(names) != 1 || names[0] != groupName {
		t.Errorf("appliance holds %v, want exactly one group named %q", names, groupName)
	}
}

// ---------------------------------------------------------------------------
// The package ships its own table-driven tests
// ---------------------------------------------------------------------------

// TestCustomGroupPackageHasTableDrivenTests checks that the implementation is
// accompanied by table-driven tests of its own.
func TestCustomGroupPackageHasTableDrivenTests(t *testing.T) {
	dir, err := contract.ModuleFile("customgroup")
	if err != nil {
		t.Fatalf("locate customgroup package: %v", err)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("read customgroup package: %v", err)
	}

	var (
		files     []string
		haveFunc  bool
		haveTable bool
	)
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), "_test.go") {
			continue
		}
		files = append(files, e.Name())
		raw, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			t.Fatalf("read %s: %v", e.Name(), err)
		}
		src := string(raw)
		if strings.Contains(src, "func Test") {
			haveFunc = true
		}
		// Accept both slice-of-struct and map-of-struct table layouts,
		// with or without gofmt's space before the brace.
		dense := strings.NewReplacer(" ", "", "\t", "", "\n", "").Replace(src)
		if strings.Contains(dense, "[]struct{") || strings.Contains(dense, "]struct{") {
			haveTable = true
		}
	}

	if len(files) == 0 {
		t.Fatal("customgroup has no _test.go file: the package must ship its own tests")
	}
	if !haveFunc {
		t.Errorf("no test function found in %v", files)
	}
	if !haveTable {
		t.Errorf("no table-driven test found in %v: expected a table of cases "+
			"such as []struct{...}{...} driving subtests", files)
	}
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

// lookup resolves a dotted path such as "membershipDefinition.includedResources"
// against a decoded JSON object.
func lookup(obj map[string]any, path string) (any, bool) {
	parts := strings.Split(path, ".")
	var cur any = obj
	for _, p := range parts {
		m, ok := cur.(map[string]any)
		if !ok {
			return nil, false
		}
		cur, ok = m[p]
		if !ok {
			return nil, false
		}
	}
	return cur, true
}

func pretty(v any) string {
	raw, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return "<unprintable>"
	}
	return string(raw)
}
