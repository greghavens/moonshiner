package mock

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
)

const (
	testUsername = "administrator@vsphere.local"
	testPassword = "VMw@re1!VMw@re1!"
	testToken    = "eyJhbGciOi.sddc-manager-access-token"

	contractPath = "../../docs/contract.json"
	fixturePath  = "../../testdata/hosts.json"
)

func start(t *testing.T, repeatBoundary bool) *Server {
	t.Helper()
	srv, err := New(Config{
		ContractPath:   contractPath,
		FixturePath:    fixturePath,
		Username:       testUsername,
		Password:       testPassword,
		AccessToken:    testToken,
		RepeatBoundary: repeatBoundary,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	t.Cleanup(srv.Close)
	return srv
}

func do(t *testing.T, srv *Server, method, target, body, contentType, authorization string) *http.Response {
	t.Helper()
	var reader *strings.Reader
	if body != "" {
		reader = strings.NewReader(body)
	}
	var req *http.Request
	var err error
	if reader == nil {
		req, err = http.NewRequest(method, srv.URL()+target, nil)
	} else {
		req, err = http.NewRequest(method, srv.URL()+target, reader)
	}
	if err != nil {
		t.Fatalf("build request: %v", err)
	}
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	if authorization != "" {
		req.Header.Set("Authorization", authorization)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatalf("%s %s: %v", method, target, err)
	}
	t.Cleanup(func() { resp.Body.Close() })
	return resp
}

func decodePage(t *testing.T, resp *http.Response) pageOfHost {
	t.Helper()
	var page pageOfHost
	if err := json.NewDecoder(resp.Body).Decode(&page); err != nil {
		t.Fatalf("decode PageOfHost: %v", err)
	}
	return page
}

func idsOf(t *testing.T, page pageOfHost) []string {
	t.Helper()
	out := make([]string, 0, len(page.Elements))
	for _, raw := range page.Elements {
		var e struct {
			ID string `json:"id"`
		}
		if err := json.Unmarshal(raw, &e); err != nil {
			t.Fatalf("decode element: %v", err)
		}
		out = append(out, e.ID)
	}
	return out
}

func TestNewRejectsAnIncompleteConfiguration(t *testing.T) {
	full := Config{
		ContractPath: contractPath,
		FixturePath:  fixturePath,
		Username:     testUsername,
		Password:     testPassword,
		AccessToken:  testToken,
	}
	cases := []struct {
		name   string
		mutate func(c *Config)
	}{
		{"no contract", func(c *Config) { c.ContractPath = "" }},
		{"no fixture", func(c *Config) { c.FixturePath = "" }},
		{"no username", func(c *Config) { c.Username = "" }},
		{"no password", func(c *Config) { c.Password = "" }},
		{"no access token", func(c *Config) { c.AccessToken = "" }},
		{"contract is missing", func(c *Config) { c.ContractPath = "../../docs/absent.json" }},
		{"fixture is missing", func(c *Config) { c.FixturePath = "../../testdata/absent.json" }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			cfg := full
			tc.mutate(&cfg)
			srv, err := New(cfg)
			if err == nil {
				srv.Close()
				t.Fatalf("New(%+v) returned no error", cfg)
			}
		})
	}
}

func TestRoutingIsPinnedToTheContract(t *testing.T) {
	srv := start(t, false)

	cases := []struct {
		name       string
		method     string
		target     string
		body       string
		ct         string
		auth       string
		wantStatus int
		wantOpID   string
	}{
		{"sign in", http.MethodPost, "/v1/tokens", `{"username":"` + testUsername + `","password":"` + testPassword + `"}`, "application/json", "", http.StatusCreated, "createToken"},
		{"list hosts", http.MethodGet, "/v1/hosts?page=0", "", "", "Bearer " + testToken, http.StatusOK, "getHosts"},
		{"unnamed path", http.MethodGet, "/v1/domains", "", "", "Bearer " + testToken, http.StatusNotFound, ""},
		{"unnamed sub path", http.MethodGet, "/v1/hosts/criteria", "", "", "Bearer " + testToken, http.StatusNotFound, ""},
		{"unnamed method", http.MethodDelete, "/v1/hosts", "", "", "Bearer " + testToken, http.StatusNotFound, ""},
		{"unnamed token method", http.MethodGet, "/v1/tokens", "", "", "", http.StatusNotFound, ""},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			before := len(srv.Requests())
			resp := do(t, srv, tc.method, tc.target, tc.body, tc.ct, tc.auth)
			if resp.StatusCode != tc.wantStatus {
				t.Errorf("status = %d, want %d", resp.StatusCode, tc.wantStatus)
			}
			log := srv.Requests()
			if len(log) != before+1 {
				t.Fatalf("log grew by %d, want 1", len(log)-before)
			}
			if got := log[len(log)-1].OperationID; got != tc.wantOpID {
				t.Errorf("operationId = %q, want %q", got, tc.wantOpID)
			}
			if got := log[len(log)-1].Status; got != tc.wantStatus {
				t.Errorf("logged status = %d, want %d", got, tc.wantStatus)
			}
		})
	}
}

func TestCreateTokenRejections(t *testing.T) {
	srv := start(t, false)

	cases := []struct {
		name       string
		body       string
		ct         string
		wantStatus int
	}{
		{"good", `{"username":"` + testUsername + `","password":"` + testPassword + `"}`, "application/json", http.StatusCreated},
		{"charset is still json", `{"username":"` + testUsername + `","password":"` + testPassword + `"}`, "application/json; charset=utf-8", http.StatusCreated},
		{"wrong media type", `{"username":"` + testUsername + `","password":"` + testPassword + `"}`, "text/plain", http.StatusBadRequest},
		{"no media type", `{"username":"` + testUsername + `","password":"` + testPassword + `"}`, "", http.StatusBadRequest},
		{"wrong password", `{"username":"` + testUsername + `","password":"nope"}`, "application/json", http.StatusBadRequest},
		{"unknown username", `{"username":"someone@vsphere.local","password":"` + testPassword + `"}`, "application/json", http.StatusBadRequest},
		{"property outside the schema", `{"username":"` + testUsername + `","password":"` + testPassword + `","tenant":"x"}`, "application/json", http.StatusBadRequest},
		{"not an object", `"token please"`, "application/json", http.StatusBadRequest},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			resp := do(t, srv, http.MethodPost, "/v1/tokens", tc.body, tc.ct, "")
			if resp.StatusCode != tc.wantStatus {
				t.Fatalf("status = %d, want %d", resp.StatusCode, tc.wantStatus)
			}
			if tc.wantStatus == http.StatusCreated {
				var pair tokenPair
				if err := json.NewDecoder(resp.Body).Decode(&pair); err != nil {
					t.Fatalf("decode TokenPair: %v", err)
				}
				if pair.AccessToken != testToken {
					t.Errorf("accessToken = %q, want %q", pair.AccessToken, testToken)
				}
				if pair.RefreshToken.ID == "" {
					t.Error("the token pair carries no refresh token id")
				}
			}
		})
	}
}

func TestGetHostsRejections(t *testing.T) {
	srv := start(t, false)

	cases := []struct {
		name       string
		target     string
		auth       string
		wantStatus int
	}{
		{"good", "/v1/hosts?page=0&size=5", "Bearer " + testToken, http.StatusOK},
		{"no authorization", "/v1/hosts?page=0", "", http.StatusUnauthorized},
		{"not a bearer token", "/v1/hosts?page=0", "Basic dXNlcjpwYXNz", http.StatusUnauthorized},
		{"unknown token", "/v1/hosts?page=0", "Bearer stale", http.StatusUnauthorized},
		{"parameter from a later revision", "/v1/hosts?pageNumber=0&pageSize=5", "Bearer " + testToken, http.StatusBadRequest},
		{"parameter outside the contract", "/v1/hosts?page=0&isStandalone=true", "Bearer " + testToken, http.StatusBadRequest},
		{"empty value", "/v1/hosts?page=0&fqdn=", "Bearer " + testToken, http.StatusBadRequest},
		{"repeated parameter", "/v1/hosts?page=0&size=2&size=3", "Bearer " + testToken, http.StatusBadRequest},
		{"page is not a number", "/v1/hosts?page=one", "Bearer " + testToken, http.StatusBadRequest},
		{"negative size", "/v1/hosts?page=0&size=-2", "Bearer " + testToken, http.StatusBadRequest},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			resp := do(t, srv, http.MethodGet, tc.target, "", "", tc.auth)
			if resp.StatusCode != tc.wantStatus {
				t.Fatalf("status = %d, want %d", resp.StatusCode, tc.wantStatus)
			}
			if tc.wantStatus >= 400 {
				var e apiError
				if err := json.NewDecoder(resp.Body).Decode(&e); err != nil {
					t.Fatalf("decode Error: %v", err)
				}
				if e.ErrorCode == "" || e.Message == "" {
					t.Errorf("failure answered with %+v, want the Error schema filled in", e)
				}
			}
		})
	}
}

func TestPagingWindows(t *testing.T) {
	cases := []struct {
		name           string
		target         string
		repeatBoundary bool
		wantIDs        []string
		wantMeta       pageMetadata
	}{
		{
			name:     "everything on one page",
			target:   "/v1/hosts?page=0",
			wantIDs:  fixtureIDs,
			wantMeta: pageMetadata{PageNumber: 0, PageSize: 14, TotalElements: 14, TotalPages: 1},
		},
		{
			name:     "first page of four",
			target:   "/v1/hosts?page=0&size=4",
			wantIDs:  fixtureIDs[0:4],
			wantMeta: pageMetadata{PageNumber: 0, PageSize: 4, TotalElements: 14, TotalPages: 4},
		},
		{
			name:     "last page is short",
			target:   "/v1/hosts?page=3&size=4",
			wantIDs:  fixtureIDs[12:14],
			wantMeta: pageMetadata{PageNumber: 3, PageSize: 2, TotalElements: 14, TotalPages: 4},
		},
		{
			name:     "past the end",
			target:   "/v1/hosts?page=9&size=4",
			wantIDs:  []string{},
			wantMeta: pageMetadata{PageNumber: 9, PageSize: 0, TotalElements: 14, TotalPages: 4},
		},
		{
			name:           "a repeated boundary element",
			target:         "/v1/hosts?page=1&size=4",
			repeatBoundary: true,
			wantIDs:        fixtureIDs[3:8],
			wantMeta:       pageMetadata{PageNumber: 1, PageSize: 5, TotalElements: 14, TotalPages: 4},
		},
		{
			name:           "the first page never repeats",
			target:         "/v1/hosts?page=0&size=4",
			repeatBoundary: true,
			wantIDs:        fixtureIDs[0:4],
			wantMeta:       pageMetadata{PageNumber: 0, PageSize: 4, TotalElements: 14, TotalPages: 4},
		},
		{
			name:     "nothing matches",
			target:   "/v1/hosts?page=0&size=4&fqdn=nowhere.vrack.vsphere.local",
			wantIDs:  []string{},
			wantMeta: pageMetadata{PageNumber: 0, PageSize: 0, TotalElements: 0, TotalPages: 0},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := start(t, tc.repeatBoundary)
			resp := do(t, srv, http.MethodGet, tc.target, "", "", "Bearer "+testToken)
			if resp.StatusCode != http.StatusOK {
				t.Fatalf("status = %d, want 200", resp.StatusCode)
			}
			page := decodePage(t, resp)
			got := idsOf(t, page)
			if len(got) != len(tc.wantIDs) {
				t.Fatalf("elements = %v, want %v", got, tc.wantIDs)
			}
			for i := range tc.wantIDs {
				if got[i] != tc.wantIDs[i] {
					t.Fatalf("elements = %v, want %v", got, tc.wantIDs)
				}
			}
			if page.PageMetadata != tc.wantMeta {
				t.Errorf("pageMetadata = %+v, want %+v", page.PageMetadata, tc.wantMeta)
			}
		})
	}
}

func TestFiltersKeepInventoryOrder(t *testing.T) {
	srv := start(t, false)

	cases := []struct {
		name    string
		target  string
		wantIDs []string
	}{
		{
			name:    "by domain",
			target:  "/v1/hosts?page=0&domainId=d1a5c0e0-0001-4c9a-9b21-1a2b3c4d5e01",
			wantIDs: []string{fixtureIDs[1], fixtureIDs[3], fixtureIDs[6], fixtureIDs[8], fixtureIDs[10]},
		},
		{
			name:    "by cluster",
			target:  "/v1/hosts?page=0&clusterId=c9f3b2a1-0003-4f1b-a7c3-4d5e6f708903",
			wantIDs: []string{fixtureIDs[4], fixtureIDs[7]},
		},
		{
			name:    "by network pool",
			target:  "/v1/hosts?page=0&networkpoolId=9b7e4d20-0001-41c8-8d55-6f7a8b9c0d01",
			wantIDs: []string{fixtureIDs[1], fixtureIDs[3], fixtureIDs[5], fixtureIDs[6], fixtureIDs[8], fixtureIDs[10]},
		},
		{
			name:    "by status",
			target:  "/v1/hosts?page=0&status=UNASSIGNED_USEABLE",
			wantIDs: []string{fixtureIDs[2], fixtureIDs[9]},
		},
		{
			name:    "by storage type",
			target:  "/v1/hosts?page=0&storageType=NFS",
			wantIDs: []string{fixtureIDs[4], fixtureIDs[7]},
		},
		{
			name:    "by bundle repository datastore",
			target:  "/v1/hosts?page=0&datastoreName=nfs-bundles-01",
			wantIDs: []string{fixtureIDs[4], fixtureIDs[7]},
		},
		{
			name:    "by fqdn",
			target:  "/v1/hosts?page=0&fqdn=esxi-9.vrack.vsphere.local",
			wantIDs: []string{fixtureIDs[4]},
		},
		{
			name:    "two filters that do not overlap",
			target:  "/v1/hosts?page=0&status=ASSIGNED&storageType=VSAN_ESA&clusterId=c9f3b2a1-0003-4f1b-a7c3-4d5e6f708903",
			wantIDs: []string{},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			resp := do(t, srv, http.MethodGet, tc.target, "", "", "Bearer "+testToken)
			if resp.StatusCode != http.StatusOK {
				t.Fatalf("status = %d, want 200", resp.StatusCode)
			}
			got := idsOf(t, decodePage(t, resp))
			if len(got) != len(tc.wantIDs) {
				t.Fatalf("elements = %v, want %v", got, tc.wantIDs)
			}
			for i := range tc.wantIDs {
				if got[i] != tc.wantIDs[i] {
					t.Fatalf("elements = %v, want %v", got, tc.wantIDs)
				}
			}
		})
	}
}

func TestRequestsIsASnapshot(t *testing.T) {
	srv := start(t, false)
	do(t, srv, http.MethodPost, "/v1/tokens", `{"username":"`+testUsername+`","password":"`+testPassword+`"}`, "application/json", "")

	first := srv.Requests()
	if len(first) != 1 {
		t.Fatalf("log has %d entries, want 1", len(first))
	}
	body := string(first[0].Body)
	first[0].Body[0] = '#'
	first[0].Header.Set("Authorization", "Bearer tampered")

	second := srv.Requests()
	if string(second[0].Body) != body {
		t.Error("Requests handed out the server's own body slice")
	}
	if second[0].Header.Get("Authorization") != "" {
		t.Error("Requests handed out the server's own header map")
	}
}

// fixtureIDs is testdata/hosts.json in the order it is stored.
var fixtureIDs = []string{
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
