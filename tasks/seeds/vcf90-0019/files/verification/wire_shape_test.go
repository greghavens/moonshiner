package verification

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"vcfhosts/pkg/client"
	"vcfhosts/pkg/mock"
)

const (
	testUsername = "administrator@vsphere.local"
	testPassword = "VMw@re1!VMw@re1!"
	accessToken  = "eyJhbGciOi.sddc-manager-access-token"

	fixturePath = "../testdata/hosts.json"

	mgmtDomainID  = "d1a5c0e0-0001-4c9a-9b21-1a2b3c4d5e01"
	mgmtClusterID = "c9f3b2a1-0001-4f1b-a7c3-4d5e6f708901"
	mgmtPoolID    = "9b7e4d20-0001-41c8-8d55-6f7a8b9c0d01"
)

// fixtureOrder is the order the inventory is stored in, which is the order the
// mock pages over. It is deliberately not the order a sweep has to emit.
var fixtureOrder = []string{
	"5f2c9b10-0007-4d3a-8e5f-0a1b2c3d4e07",
	"5f2c9b10-0001-4d3a-8e5f-0a1b2c3d4e01",
	"5f2c9b10-0012-4d3a-8e5f-0a1b2c3d4e12",
	"5f2c9b10-0003-4d3a-8e5f-0a1b2c3d4e03",
	"5f2c9b10-0009-4d3a-8e5f-0a1b2c3d4e09",
	"5f2c9b10-0014-4d3a-8e5f-0a1b2c3d4e14",
	"5f2c9b10-0002-4d3a-8e5f-0a1b2c3d4e02",
	"5f2c9b10-0010-4d3a-8e5f-0a1b2c3d4e10",
	"5f2c9b10-0004-4d3a-8e5f-0a1b2c3d4e04",
	"5f2c9b10-0013-4d3a-8e5f-0a1b2c3d4e13",
	"5f2c9b10-0005-4d3a-8e5f-0a1b2c3d4e05",
	"5f2c9b10-0011-4d3a-8e5f-0a1b2c3d4e11",
	"5f2c9b10-0006-4d3a-8e5f-0a1b2c3d4e06",
	"5f2c9b10-0008-4d3a-8e5f-0a1b2c3d4e08",
}

// emitOrder is every host in the inventory, ascending by fqdn.
var emitOrder = []string{
	"5f2c9b10-0003-4d3a-8e5f-0a1b2c3d4e03", // ESXi-05.vrack.vsphere.local
	"5f2c9b10-0004-4d3a-8e5f-0a1b2c3d4e04", // ESXi-06.vrack.vsphere.local
	"5f2c9b10-0005-4d3a-8e5f-0a1b2c3d4e05", // esx-mgmt-01.vrack.vsphere.local
	"5f2c9b10-0001-4d3a-8e5f-0a1b2c3d4e01", // esxi-01.vrack.vsphere.local
	"5f2c9b10-0002-4d3a-8e5f-0a1b2c3d4e02", // esxi-02.vrack.vsphere.local
	"5f2c9b10-0006-4d3a-8e5f-0a1b2c3d4e06", // esxi-03.vrack.vsphere.local
	"5f2c9b10-0008-4d3a-8e5f-0a1b2c3d4e08", // esxi-04.vrack.vsphere.local
	"5f2c9b10-0011-4d3a-8e5f-0a1b2c3d4e11", // esxi-10.vrack.vsphere.local
	"5f2c9b10-0007-4d3a-8e5f-0a1b2c3d4e07", // esxi-11.vrack.vsphere.local
	"5f2c9b10-0010-4d3a-8e5f-0a1b2c3d4e10", // esxi-12.vrack.vsphere.local
	"5f2c9b10-0012-4d3a-8e5f-0a1b2c3d4e12", // esxi-20.vrack.vsphere.local
	"5f2c9b10-0013-4d3a-8e5f-0a1b2c3d4e13", // esxi-21.vrack.vsphere.local
	"5f2c9b10-0014-4d3a-8e5f-0a1b2c3d4e14", // esxi-30.vrack.vsphere.local
	"5f2c9b10-0009-4d3a-8e5f-0a1b2c3d4e09", // esxi-9.vrack.vsphere.local
}

func newServer(t *testing.T, repeatBoundary bool) *mock.Server {
	t.Helper()
	srv, err := mock.New(mock.Config{
		ContractPath:   contractPath,
		FixturePath:    fixturePath,
		Username:       testUsername,
		Password:       testPassword,
		AccessToken:    accessToken,
		RepeatBoundary: repeatBoundary,
	})
	if err != nil {
		t.Fatalf("start mock: %v", err)
	}
	t.Cleanup(srv.Close)
	if !strings.HasPrefix(srv.URL(), "http://127.0.0.1:") {
		t.Fatalf("mock must listen on loopback, got %q", srv.URL())
	}
	return srv
}

func newClient(t *testing.T, srv *mock.Server) *client.Client {
	t.Helper()
	c, err := client.New(client.Config{
		BaseURL:  srv.URL(),
		Username: testUsername,
		Password: testPassword,
	})
	if err != nil {
		t.Fatalf("new client: %v", err)
	}
	return c
}

func queryKeys(t *testing.T, raw string) []string {
	t.Helper()
	if raw == "" {
		return nil
	}
	values, err := url.ParseQuery(raw)
	if err != nil {
		t.Fatalf("parse query %q: %v", raw, err)
	}
	keys := make([]string, 0, len(values))
	for k, v := range values {
		if len(v) != 1 {
			t.Errorf("query %q repeats parameter %q", raw, k)
		}
		for _, item := range v {
			if item == "" {
				t.Errorf("query %q sends %q with an empty value; unset optional parameters must be omitted", raw, k)
			}
		}
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func assertKeys(t *testing.T, what string, got, want []string) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("%s: parameters = %v, want exactly %v", what, got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("%s: parameters = %v, want exactly %v", what, got, want)
		}
	}
}

func queryValue(t *testing.T, raw, key string) string {
	t.Helper()
	values, err := url.ParseQuery(raw)
	if err != nil {
		t.Fatalf("parse query %q: %v", raw, err)
	}
	return values.Get(key)
}

func assertCreateToken(t *testing.T, r mock.Request) {
	t.Helper()
	if r.OperationID != "createToken" {
		t.Fatalf("first request operation = %q, want createToken", r.OperationID)
	}
	if r.Method != http.MethodPost || r.Path != "/v1/tokens" {
		t.Fatalf("createToken sent as %s %s, want POST /v1/tokens", r.Method, r.Path)
	}
	if r.Status != http.StatusCreated {
		t.Fatalf("createToken answered %d, want 201", r.Status)
	}
	if got := r.Header.Get("Content-Type"); got != "application/json" {
		t.Errorf("createToken Content-Type = %q, want %q", got, "application/json")
	}
	if got := r.Header.Get("Authorization"); got != "" {
		t.Errorf("createToken must not carry an Authorization header, got %q", got)
	}
	if r.RawQuery != "" {
		t.Errorf("createToken must not carry query parameters, got %q", r.RawQuery)
	}

	var fields map[string]json.RawMessage
	if err := json.Unmarshal(r.Body, &fields); err != nil {
		t.Fatalf("createToken body %q is not a JSON object: %v", string(r.Body), err)
	}
	keys := make([]string, 0, len(fields))
	for k := range fields {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	assertKeys(t, "createToken body", keys, []string{"password", "username"})

	var spec struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	if err := json.Unmarshal(r.Body, &spec); err != nil {
		t.Fatalf("createToken body: %v", err)
	}
	if spec.Username != testUsername || spec.Password != testPassword {
		t.Errorf("createToken body carried %q/%q, want the configured credentials", spec.Username, spec.Password)
	}
}

func assertHostsRequest(t *testing.T, index int, r mock.Request) {
	t.Helper()
	if r.OperationID != "getHosts" {
		t.Fatalf("request %d operation = %q, want getHosts", index, r.OperationID)
	}
	if r.Method != http.MethodGet || r.Path != "/v1/hosts" {
		t.Fatalf("request %d sent as %s %s, want GET /v1/hosts", index, r.Method, r.Path)
	}
	if len(r.Body) != 0 {
		t.Errorf("request %d carried a body %q, want none", index, string(r.Body))
	}
	if got := r.Header.Get("Content-Type"); got != "" {
		t.Errorf("request %d carried Content-Type %q; a request without a body must not declare one", index, got)
	}
	if got, want := r.Header.Get("Authorization"), "Bearer "+accessToken; got != want {
		t.Errorf("request %d Authorization = %q, want %q", index, got, want)
	}
	if strings.Contains(r.RawQuery, "=&") || strings.HasSuffix(r.RawQuery, "=") {
		t.Errorf("request %d raw query %q sends a parameter with an empty value", index, r.RawQuery)
	}
}

func TestSweepIsCompleteAndStablyOrdered(t *testing.T) {
	srv := newServer(t, true)
	c := newClient(t, srv)

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	got, err := c.ListHosts(ctx, client.Filter{PageSize: 4})
	if err != nil {
		t.Fatalf("ListHosts: %v", err)
	}

	gotIDs := make([]string, 0, len(got))
	for _, h := range got {
		gotIDs = append(gotIDs, h.ID)
	}
	if len(gotIDs) != len(emitOrder) {
		t.Fatalf("swept %d hosts, want %d (every host exactly once):\n%v", len(gotIDs), len(emitOrder), gotIDs)
	}
	for i := range emitOrder {
		if gotIDs[i] != emitOrder[i] {
			t.Fatalf("hosts emitted in the wrong order\n got: %v\nwant: %v", gotIDs, emitOrder)
		}
	}

	// The fqdn ordering is decided byte by byte, so the upper-case names sort
	// ahead of the lower-case ones.
	for i := 1; i < len(got); i++ {
		if got[i-1].FQDN > got[i].FQDN {
			t.Fatalf("host %d fqdn %q sorts after host %d fqdn %q", i-1, got[i-1].FQDN, i, got[i].FQDN)
		}
	}

	log := srv.Requests()
	if len(log) != 5 {
		t.Fatalf("request log has %d entries, want 5 (1 token + 4 pages):\n%s", len(log), formatLog(log))
	}
	assertCreateToken(t, log[0])
	for i, r := range log[1:] {
		assertHostsRequest(t, i+1, r)
		assertKeys(t, "getHosts query", queryKeys(t, r.RawQuery), []string{"page", "size"})
		if got, want := queryValue(t, r.RawQuery, "page"), strconv.Itoa(i); got != want {
			t.Errorf("request %d page = %q, want %q", i+1, got, want)
		}
		if got := queryValue(t, r.RawQuery, "size"); got != "4" {
			t.Errorf("request %d size = %q, want %q", i+1, got, "4")
		}
		if r.Status != http.StatusOK {
			t.Errorf("request %d answered %d, want 200", i+1, r.Status)
		}
	}
}

func TestUnsetOptionalParametersAreOmitted(t *testing.T) {
	cases := []struct {
		name     string
		filter   client.Filter
		wantKeys []string
		wantRaw  string
		wantLen  int
		wantReqs int
	}{
		{
			name:     "no filter and no page size",
			filter:   client.Filter{},
			wantKeys: []string{"page"},
			wantRaw:  "page=0",
			wantLen:  14,
			wantReqs: 2,
		},
		{
			name:     "paged only",
			filter:   client.Filter{PageSize: 5},
			wantKeys: []string{"page", "size"},
			wantLen:  14,
			wantReqs: 4,
		},
		{
			name:     "one filter set",
			filter:   client.Filter{DomainID: mgmtDomainID, PageSize: 10},
			wantKeys: []string{"domainId", "page", "size"},
			wantLen:  5,
			wantReqs: 2,
		},
		{
			name:     "two filters set",
			filter:   client.Filter{Status: "ASSIGNED", StorageType: "VSAN_ESA", PageSize: 3},
			wantKeys: []string{"page", "size", "status", "storageType"},
			wantLen:  4,
			wantReqs: 3,
		},
		{
			name: "every filter set",
			filter: client.Filter{
				FQDN:          "esxi-01.vrack.vsphere.local",
				Status:        "ASSIGNED",
				DomainID:      mgmtDomainID,
				ClusterID:     mgmtClusterID,
				NetworkPoolID: mgmtPoolID,
				StorageType:   "VSAN",
				DatastoreName: "vsanDatastore",
				PageSize:      10,
			},
			wantKeys: []string{"clusterId", "datastoreName", "domainId", "fqdn", "networkpoolId", "page", "size", "status", "storageType"},
			wantLen:  1,
			wantReqs: 2,
		},
		{
			name:     "nothing matches",
			filter:   client.Filter{FQDN: "esxi-99.vrack.vsphere.local", PageSize: 5},
			wantKeys: []string{"fqdn", "page", "size"},
			wantLen:  0,
			wantReqs: 2,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newServer(t, false)
			c := newClient(t, srv)

			got, err := c.ListHosts(context.Background(), tc.filter)
			if err != nil {
				t.Fatalf("ListHosts: %v", err)
			}
			if len(got) != tc.wantLen {
				t.Fatalf("swept %d hosts, want %d", len(got), tc.wantLen)
			}

			log := srv.Requests()
			if len(log) != tc.wantReqs {
				t.Fatalf("request log has %d entries, want %d:\n%s", len(log), tc.wantReqs, formatLog(log))
			}
			assertCreateToken(t, log[0])

			for i, r := range log[1:] {
				assertHostsRequest(t, i+1, r)
				assertKeys(t, "getHosts query", queryKeys(t, r.RawQuery), tc.wantKeys)
				if tc.wantRaw != "" && r.RawQuery != tc.wantRaw {
					t.Errorf("request %d raw query = %q, want %q", i+1, r.RawQuery, tc.wantRaw)
				}
			}
		})
	}
}

func TestMockPagesTheContractWay(t *testing.T) {
	srv := newServer(t, true)

	type page struct {
		elements   []string
		pageNumber int
		pageSize   int
		totalPages int
		totalCount int
	}

	get := func(t *testing.T, target string) page {
		t.Helper()
		req, err := http.NewRequest(http.MethodGet, srv.URL()+target, nil)
		if err != nil {
			t.Fatalf("build request: %v", err)
		}
		req.Header.Set("Authorization", "Bearer "+accessToken)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatalf("GET %s: %v", target, err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("GET %s answered %d, want 200", target, resp.StatusCode)
		}
		var body struct {
			Elements []struct {
				ID string `json:"id"`
			} `json:"elements"`
			PageMetadata struct {
				PageNumber    int `json:"pageNumber"`
				PageSize      int `json:"pageSize"`
				TotalElements int `json:"totalElements"`
				TotalPages    int `json:"totalPages"`
			} `json:"pageMetadata"`
		}
		if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
			t.Fatalf("GET %s: decode PageOfHost: %v", target, err)
		}
		ids := make([]string, 0, len(body.Elements))
		for _, e := range body.Elements {
			ids = append(ids, e.ID)
		}
		return page{ids, body.PageMetadata.PageNumber, body.PageMetadata.PageSize,
			body.PageMetadata.TotalPages, body.PageMetadata.TotalElements}
	}

	first := get(t, "/v1/hosts?page=0&size=4")
	if want := fixtureOrder[0:4]; !equal(first.elements, want) {
		t.Fatalf("page 0 elements = %v, want the inventory order %v", first.elements, want)
	}
	if first.pageNumber != 0 || first.pageSize != 4 || first.totalPages != 4 || first.totalCount != 14 {
		t.Fatalf("page 0 metadata = pageNumber %d, pageSize %d, totalPages %d, totalElements %d; want 0, 4, 4, 14",
			first.pageNumber, first.pageSize, first.totalPages, first.totalCount)
	}

	second := get(t, "/v1/hosts?page=1&size=4")
	if want := fixtureOrder[3:8]; !equal(second.elements, want) {
		t.Fatalf("page 1 elements = %v, want %v: a page after the first repeats the last element of the one before it",
			second.elements, want)
	}
	if second.pageNumber != 1 || second.pageSize != len(second.elements) {
		t.Fatalf("page 1 metadata = pageNumber %d, pageSize %d; want 1 and the number of elements on the page (%d)",
			second.pageNumber, second.pageSize, len(second.elements))
	}

	last := get(t, "/v1/hosts?page=3&size=4")
	if want := fixtureOrder[11:14]; !equal(last.elements, want) {
		t.Fatalf("page 3 elements = %v, want %v", last.elements, want)
	}

	// No size at all is a single page holding everything.
	all := get(t, "/v1/hosts?page=0")
	if !equal(all.elements, fixtureOrder) {
		t.Fatalf("unsized page elements = %v, want the whole inventory", all.elements)
	}
	if all.totalPages != 1 || all.totalCount != 14 {
		t.Fatalf("unsized page metadata = totalPages %d, totalElements %d; want 1 and 14", all.totalPages, all.totalCount)
	}

	// Filtering keeps the inventory order.
	filtered := get(t, "/v1/hosts?page=0&domainId="+mgmtDomainID)
	want := []string{
		"5f2c9b10-0001-4d3a-8e5f-0a1b2c3d4e01",
		"5f2c9b10-0003-4d3a-8e5f-0a1b2c3d4e03",
		"5f2c9b10-0002-4d3a-8e5f-0a1b2c3d4e02",
		"5f2c9b10-0004-4d3a-8e5f-0a1b2c3d4e04",
		"5f2c9b10-0005-4d3a-8e5f-0a1b2c3d4e05",
	}
	if !equal(filtered.elements, want) {
		t.Fatalf("domainId page elements = %v, want %v", filtered.elements, want)
	}
	if filtered.totalCount != 5 {
		t.Fatalf("domainId totalElements = %d, want 5", filtered.totalCount)
	}
}

func TestMockServesOnlyTheContractOperations(t *testing.T) {
	srv := newServer(t, false)
	base := srv.URL()

	do := func(t *testing.T, method, target string, body io.Reader, header map[string]string) *http.Response {
		t.Helper()
		req, err := http.NewRequest(method, base+target, body)
		if err != nil {
			t.Fatalf("build request: %v", err)
		}
		for k, v := range header {
			req.Header.Set(k, v)
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatalf("%s %s: %v", method, target, err)
		}
		t.Cleanup(func() { resp.Body.Close() })
		return resp
	}

	authorized := map[string]string{"Authorization": "Bearer " + accessToken}
	jsonBody := map[string]string{"Content-Type": "application/json"}

	cases := []struct {
		name       string
		method     string
		target     string
		body       io.Reader
		header     map[string]string
		wantStatus int
		wantOpID   string
	}{
		{
			name:       "path outside the contract",
			method:     http.MethodGet,
			target:     "/v1/domains",
			header:     authorized,
			wantStatus: http.StatusNotFound,
		},
		{
			name:       "method outside the contract",
			method:     http.MethodPost,
			target:     "/v1/hosts",
			body:       strings.NewReader(`[]`),
			header:     map[string]string{"Authorization": "Bearer " + accessToken, "Content-Type": "application/json"},
			wantStatus: http.StatusNotFound,
		},
		{
			name:       "method differs from the contract by case",
			method:     "get",
			target:     "/v1/hosts?page=0",
			header:     authorized,
			wantStatus: http.StatusNotFound,
		},
		{
			name:       "host criteria is outside the contract",
			method:     http.MethodGet,
			target:     "/v1/hosts/criteria",
			header:     authorized,
			wantStatus: http.StatusNotFound,
		},
		{
			name:       "paging parameters from a later revision of the specification",
			method:     http.MethodGet,
			target:     "/v1/hosts?pageNumber=0&pageSize=5",
			header:     authorized,
			wantStatus: http.StatusBadRequest,
			wantOpID:   "getHosts",
		},
		{
			name:       "query parameter outside the contract",
			method:     http.MethodGet,
			target:     "/v1/hosts?page=0&isStandalone=true",
			header:     authorized,
			wantStatus: http.StatusBadRequest,
			wantOpID:   "getHosts",
		},
		{
			name:       "optional parameter sent empty",
			method:     http.MethodGet,
			target:     "/v1/hosts?page=0&domainId=",
			header:     authorized,
			wantStatus: http.StatusBadRequest,
			wantOpID:   "getHosts",
		},
		{
			name:       "repeated parameter",
			method:     http.MethodGet,
			target:     "/v1/hosts?page=0&page=1",
			header:     authorized,
			wantStatus: http.StatusBadRequest,
			wantOpID:   "getHosts",
		},
		{
			name:       "negative page",
			method:     http.MethodGet,
			target:     "/v1/hosts?page=-1",
			header:     authorized,
			wantStatus: http.StatusBadRequest,
			wantOpID:   "getHosts",
		},
		{
			name:       "page is not an integer",
			method:     http.MethodGet,
			target:     "/v1/hosts?page=first",
			header:     authorized,
			wantStatus: http.StatusBadRequest,
			wantOpID:   "getHosts",
		},
		{
			name:       "missing bearer token",
			method:     http.MethodGet,
			target:     "/v1/hosts?page=0",
			wantStatus: http.StatusUnauthorized,
			wantOpID:   "getHosts",
		},
		{
			name:       "authentication is checked before the query",
			method:     http.MethodGet,
			target:     "/v1/hosts?pageNumber=0&pageSize=5",
			wantStatus: http.StatusUnauthorized,
			wantOpID:   "getHosts",
		},
		{
			name:       "unknown bearer token",
			method:     http.MethodGet,
			target:     "/v1/hosts?page=0",
			header:     map[string]string{"Authorization": "Bearer not-the-access-token"},
			wantStatus: http.StatusUnauthorized,
			wantOpID:   "getHosts",
		},
		{
			name:       "wrong password",
			method:     http.MethodPost,
			target:     "/v1/tokens",
			body:       strings.NewReader(`{"username":"` + testUsername + `","password":"wrong"}`),
			header:     jsonBody,
			wantStatus: http.StatusBadRequest,
			wantOpID:   "createToken",
		},
		{
			name:       "body field outside the contract",
			method:     http.MethodPost,
			target:     "/v1/tokens",
			body:       strings.NewReader(`{"username":"` + testUsername + `","password":"` + testPassword + `","tenant":"vsphere.local"}`),
			header:     jsonBody,
			wantStatus: http.StatusBadRequest,
			wantOpID:   "createToken",
		},
		{
			name:       "wrong media type",
			method:     http.MethodPost,
			target:     "/v1/tokens",
			body:       strings.NewReader(`{"username":"` + testUsername + `","password":"` + testPassword + `"}`),
			header:     map[string]string{"Content-Type": "text/plain"},
			wantStatus: http.StatusBadRequest,
			wantOpID:   "createToken",
		},
		{
			name:       "a good sign in",
			method:     http.MethodPost,
			target:     "/v1/tokens",
			body:       strings.NewReader(`{"username":"` + testUsername + `","password":"` + testPassword + `"}`),
			header:     jsonBody,
			wantStatus: http.StatusCreated,
			wantOpID:   "createToken",
		},
		{
			name:       "the contract media type with a charset",
			method:     http.MethodPost,
			target:     "/v1/tokens",
			body:       strings.NewReader(`{"username":"` + testUsername + `","password":"` + testPassword + `"}`),
			header:     map[string]string{"Content-Type": "application/json; charset=utf-8"},
			wantStatus: http.StatusCreated,
			wantOpID:   "createToken",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			before := len(srv.Requests())
			resp := do(t, tc.method, tc.target, tc.body, tc.header)
			if resp.StatusCode != tc.wantStatus {
				t.Errorf("%s %s answered %d, want %d", tc.method, tc.target, resp.StatusCode, tc.wantStatus)
			}
			log := srv.Requests()
			if len(log) != before+1 {
				t.Fatalf("request log grew by %d, want 1: every request must be logged", len(log)-before)
			}
			last := log[len(log)-1]
			if last.OperationID != tc.wantOpID {
				t.Errorf("logged operationId = %q, want %q", last.OperationID, tc.wantOpID)
			}
			if last.Status != tc.wantStatus {
				t.Errorf("logged status = %d, want %d", last.Status, tc.wantStatus)
			}
			if last.Index != len(log)-1 {
				t.Errorf("logged index = %d, want %d", last.Index, len(log)-1)
			}
		})
	}
}

func TestRequestLogIsASnapshot(t *testing.T) {
	srv := newServer(t, false)
	c := newClient(t, srv)

	if _, err := c.ListHosts(context.Background(), client.Filter{PageSize: 5}); err != nil {
		t.Fatalf("ListHosts: %v", err)
	}
	first := srv.Requests()
	if len(first) == 0 {
		t.Fatal("request log is empty")
	}
	originalBody := string(first[0].Body)
	if originalBody == "" {
		t.Fatal("createToken was logged without a body")
	}
	first[0].OperationID = "tampered"
	first[0].Body[0] = '#'

	second := srv.Requests()
	if second[0].OperationID == "tampered" {
		t.Error("Requests must return a snapshot the caller cannot mutate")
	}
	if string(second[0].Body) != originalBody {
		t.Error("Requests must return request bodies the caller cannot mutate")
	}
}

func TestClientRejectsAnIncompleteConfiguration(t *testing.T) {
	srv := newServer(t, false)

	cases := []struct {
		name string
		cfg  client.Config
	}{
		{"no base url", client.Config{Username: testUsername, Password: testPassword}},
		{"no username", client.Config{BaseURL: srv.URL(), Password: testPassword}},
		{"no password", client.Config{BaseURL: srv.URL(), Username: testUsername}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := client.New(tc.cfg); err == nil {
				t.Errorf("client.New(%+v) returned no error", tc.cfg)
			}
		})
	}
}

func TestMockRejectsAnIncompleteConfiguration(t *testing.T) {
	full := mock.Config{
		ContractPath: contractPath,
		FixturePath:  fixturePath,
		Username:     testUsername,
		Password:     testPassword,
		AccessToken:  accessToken,
	}
	cases := []struct {
		name   string
		mutate func(*mock.Config)
	}{
		{"no contract path", func(c *mock.Config) { c.ContractPath = "" }},
		{"no fixture path", func(c *mock.Config) { c.FixturePath = "" }},
		{"no username", func(c *mock.Config) { c.Username = "" }},
		{"no password", func(c *mock.Config) { c.Password = "" }},
		{"no access token", func(c *mock.Config) { c.AccessToken = "" }},
		{"missing contract", func(c *mock.Config) { c.ContractPath = "../docs/missing.json" }},
		{"missing fixture", func(c *mock.Config) { c.FixturePath = "../testdata/missing.json" }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := full
			tc.mutate(&cfg)
			srv, err := mock.New(cfg)
			if err == nil {
				srv.Close()
				t.Fatalf("mock.New(%+v) returned no error", cfg)
			}
		})
	}
}

func TestClientBreaksFQDNTiesByIDAndKeepsAbsentReferencesEmpty(t *testing.T) {
	fixture := `[
  {"id":"host-z","fqdn":"same.example","ipAddresses":[{"ipAddress":"192.0.2.2"}]},
  {"id":"host-a","fqdn":"same.example","ipAddresses":[{"ipAddress":"192.0.2.1"}]},
  {"id":"host-none","fqdn":"zz.example","ipAddresses":[]}
]`
	path := t.TempDir() + "/hosts.json"
	if err := os.WriteFile(path, []byte(fixture), 0o600); err != nil {
		t.Fatalf("write fixture: %v", err)
	}
	srv, err := mock.New(mock.Config{
		ContractPath: contractPath,
		FixturePath:  path,
		Username:     testUsername,
		Password:     testPassword,
		AccessToken:  accessToken,
	})
	if err != nil {
		t.Fatalf("start mock: %v", err)
	}
	t.Cleanup(srv.Close)
	c := newClient(t, srv)

	hosts, err := c.ListHosts(context.Background(), client.Filter{})
	if err != nil {
		t.Fatalf("ListHosts: %v", err)
	}
	if len(hosts) != 3 {
		t.Fatalf("hosts = %+v, want three", hosts)
	}
	if hosts[0].ID != "host-a" || hosts[1].ID != "host-z" || hosts[2].ID != "host-none" {
		t.Fatalf("host ids = [%s %s %s], want [host-a host-z host-none]", hosts[0].ID, hosts[1].ID, hosts[2].ID)
	}
	if hosts[2].DomainID != "" || hosts[2].ClusterID != "" || hosts[2].NetworkPoolID != "" {
		t.Errorf("absent references mapped to domain=%q cluster=%q networkPool=%q, want empty strings",
			hosts[2].DomainID, hosts[2].ClusterID, hosts[2].NetworkPoolID)
	}
	if !equal(hosts[0].IPAddresses, []string{"192.0.2.1"}) {
		t.Errorf("IPAddresses = %v, want record order preserved", hosts[0].IPAddresses)
	}
}

func TestClientErrorCarriesStatusAndErrorBody(t *testing.T) {
	srv := newServer(t, false)
	c, err := client.New(client.Config{
		BaseURL:  srv.URL(),
		Username: testUsername,
		Password: "wrong-password",
	})
	if err != nil {
		t.Fatalf("client.New: %v", err)
	}
	_, err = c.ListHosts(context.Background(), client.Filter{})
	if err == nil {
		t.Fatal("ListHosts returned no error")
	}
	message := err.Error()
	for _, want := range []string{"400 Bad Request", "INVALID_CREDENTIALS", "credentials are not valid"} {
		if !strings.Contains(message, want) {
			t.Errorf("error %q does not carry %q", message, want)
		}
	}
}

func equal(got, want []string) bool {
	if len(got) != len(want) {
		return false
	}
	for i := range want {
		if got[i] != want[i] {
			return false
		}
	}
	return true
}

func formatLog(log []mock.Request) string {
	var b strings.Builder
	for _, r := range log {
		b.WriteString(r.Method)
		b.WriteString(" ")
		b.WriteString(r.Path)
		if r.RawQuery != "" {
			b.WriteString("?")
			b.WriteString(r.RawQuery)
		}
		b.WriteString(" -> ")
		b.WriteString(r.OperationID)
		b.WriteString(" ")
		b.WriteString(http.StatusText(r.Status))
		b.WriteString("\n")
	}
	return b.String()
}
