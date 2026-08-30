package verification

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"path/filepath"
	"sort"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"vcfsddc/pkg/client"
	"vcfsddc/pkg/mock"
)

const (
	testUsername   = "administrator@vsphere.local"
	testPassword   = "VMw@re1!VMw@re1!"
	initialToken   = "eyJhbGciOi.initial-access-token"
	refreshedToken = "eyJhbGciOi.refreshed-access-token"
	refreshTokenID = "5b1e6bfa-6a1d-4f6f-9a2e-2f0a4b5c6d7e"
)

// esxiCredentialIDs are the fixture credentials whose resourceType is ESXI, in
// fixture order.
var esxiCredentialIDs = []string{
	"c1e0a2b4-0001-4a11-9f01-0a1b2c3d4e01",
	"c1e0a2b4-0002-4a11-9f01-0a1b2c3d4e02",
	"c1e0a2b4-0004-4a11-9f01-0a1b2c3d4e04",
	"c1e0a2b4-0006-4a11-9f01-0a1b2c3d4e06",
	"c1e0a2b4-0007-4a11-9f01-0a1b2c3d4e07",
	"c1e0a2b4-0009-4a11-9f01-0a1b2c3d4e09",
	"c1e0a2b4-0011-4a11-9f01-0a1b2c3d4e11",
	"c1e0a2b4-0012-4a11-9f01-0a1b2c3d4e12",
}

func newServer(t *testing.T, expireAfter int) *mock.Server {
	t.Helper()
	srv, err := mock.New(mock.Config{
		ContractPath:   contractPath,
		FixturePath:    "../testdata/credentials.json",
		Username:       testUsername,
		Password:       testPassword,
		AccessToken:    initialToken,
		RefreshedToken: refreshedToken,
		RefreshTokenID: refreshTokenID,
		ExpireAfter:    expireAfter,
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

func TestTokenExpiryIsRecoveredWithoutLosingWork(t *testing.T) {
	srv := newServer(t, 2)
	c := newClient(t, srv)

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	got, err := c.ListCredentials(ctx, client.Filter{ResourceType: "ESXI", PageSize: 2})
	if err != nil {
		t.Fatalf("ListCredentials: %v", err)
	}

	gotIDs := make([]string, 0, len(got))
	for _, cred := range got {
		gotIDs = append(gotIDs, cred.ID)
		if cred.Resource.ResourceType != "ESXI" {
			t.Errorf("credential %s has resourceType %q, want ESXI", cred.ID, cred.Resource.ResourceType)
		}
	}
	if len(gotIDs) != len(esxiCredentialIDs) {
		t.Fatalf("collected %d credentials %v, want %d", len(gotIDs), gotIDs, len(esxiCredentialIDs))
	}
	for i := range esxiCredentialIDs {
		if gotIDs[i] != esxiCredentialIDs[i] {
			t.Fatalf("collected credential ids = %v, want %v", gotIDs, esxiCredentialIDs)
		}
	}

	log := srv.Requests()
	if len(log) != 7 {
		t.Fatalf("request log has %d entries, want 7 (1 token + 2 pages + 1 expired page + 1 refresh + the retried page + the last page):\n%s",
			len(log), formatLog(log))
	}

	assertCreateToken(t, log[0])

	type pageExpectation struct {
		index      int
		pageNumber string
		bearer     string
		status     int
	}
	pages := []pageExpectation{
		{1, "0", initialToken, http.StatusOK},
		{2, "1", initialToken, http.StatusOK},
		{3, "2", initialToken, http.StatusUnauthorized},
		{5, "2", refreshedToken, http.StatusOK},
		{6, "3", refreshedToken, http.StatusOK},
	}
	for _, want := range pages {
		r := log[want.index]
		if r.OperationID != "getCredentials" {
			t.Fatalf("request %d operation = %q, want getCredentials\n%s", want.index, r.OperationID, formatLog(log))
		}
		if r.Method != http.MethodGet || r.Path != "/v1/credentials" {
			t.Fatalf("request %d sent as %s %s, want GET /v1/credentials", want.index, r.Method, r.Path)
		}
		if len(r.Body) != 0 {
			t.Errorf("request %d carried a body %q, want none", want.index, string(r.Body))
		}
		assertKeys(t, "getCredentials query", queryKeys(t, r.RawQuery), []string{"pageNumber", "pageSize", "resourceType"})
		if got := queryValue(t, r.RawQuery, "pageNumber"); got != want.pageNumber {
			t.Errorf("request %d pageNumber = %q, want %q", want.index, got, want.pageNumber)
		}
		if got := queryValue(t, r.RawQuery, "pageSize"); got != "2" {
			t.Errorf("request %d pageSize = %q, want %q", want.index, got, "2")
		}
		if got := queryValue(t, r.RawQuery, "resourceType"); got != "ESXI" {
			t.Errorf("request %d resourceType = %q, want ESXI", want.index, got)
		}
		if got, wantHeader := r.Header.Get("Authorization"), "Bearer "+want.bearer; got != wantHeader {
			t.Errorf("request %d Authorization = %q, want %q", want.index, got, wantHeader)
		}
		if r.Status != want.status {
			t.Errorf("request %d answered %d, want %d", want.index, r.Status, want.status)
		}
	}

	refresh := log[4]
	if refresh.OperationID != "refreshAccessToken" {
		t.Fatalf("request 4 operation = %q, want refreshAccessToken\n%s", refresh.OperationID, formatLog(log))
	}
	if refresh.Method != http.MethodPatch || refresh.Path != "/v1/tokens/access-token/refresh" {
		t.Fatalf("refresh sent as %s %s, want PATCH /v1/tokens/access-token/refresh", refresh.Method, refresh.Path)
	}
	if refresh.Status != http.StatusOK {
		t.Errorf("refresh answered %d, want 200", refresh.Status)
	}
	if got := refresh.Header.Get("Content-Type"); got != "application/json" {
		t.Errorf("refresh Content-Type = %q, want %q", got, "application/json")
	}
	if refresh.RawQuery != "" {
		t.Errorf("refresh must not carry query parameters, got %q", refresh.RawQuery)
	}
	body := strings.TrimSpace(string(refresh.Body))
	var asString string
	if err := json.Unmarshal([]byte(body), &asString); err != nil {
		t.Fatalf("refresh body %q must be a bare JSON string holding the refresh token id: %v", body, err)
	}
	if asString != refreshTokenID {
		t.Errorf("refresh body carried %q, want the refresh token id %q", asString, refreshTokenID)
	}

	var tokenCalls, refreshCalls int
	for _, r := range log {
		switch r.OperationID {
		case "createToken":
			tokenCalls++
		case "refreshAccessToken":
			refreshCalls++
		}
	}
	if tokenCalls != 1 {
		t.Errorf("createToken called %d times, want 1: an expired access token is refreshed, not re-issued", tokenCalls)
	}
	if refreshCalls != 1 {
		t.Errorf("refreshAccessToken called %d times, want 1", refreshCalls)
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
			name:     "only pagination",
			filter:   client.Filter{PageSize: 3},
			wantKeys: []string{"pageNumber", "pageSize"},
			wantLen:  12,
			wantReqs: 5,
		},
		{
			name:     "no page size falls back to the server default",
			filter:   client.Filter{},
			wantKeys: []string{"pageNumber"},
			wantRaw:  "pageNumber=0",
			wantLen:  12,
			wantReqs: 2,
		},
		{
			name:     "one filter set",
			filter:   client.Filter{DomainName: "wld-01", PageSize: 10},
			wantKeys: []string{"domainName", "pageNumber", "pageSize"},
			wantLen:  5,
			wantReqs: 2,
		},
		{
			name:     "every filter set",
			filter:   client.Filter{ResourceName: "esxi-01.vrack.vsphere.local", ResourceType: "ESXI", DomainName: "mgmt-domain", AccountType: "USER", PageSize: 10},
			wantKeys: []string{"accountType", "domainName", "pageNumber", "pageSize", "resourceName", "resourceType"},
			wantLen:  1,
			wantReqs: 2,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newServer(t, 1000)
			c := newClient(t, srv)

			got, err := c.ListCredentials(context.Background(), tc.filter)
			if err != nil {
				t.Fatalf("ListCredentials: %v", err)
			}
			if len(got) != tc.wantLen {
				t.Fatalf("collected %d credentials, want %d", len(got), tc.wantLen)
			}

			log := srv.Requests()
			if len(log) != tc.wantReqs {
				t.Fatalf("request log has %d entries, want %d:\n%s", len(log), tc.wantReqs, formatLog(log))
			}
			assertCreateToken(t, log[0])

			for i, r := range log[1:] {
				if r.OperationID != "getCredentials" {
					t.Fatalf("request %d operation = %q, want getCredentials", i+1, r.OperationID)
				}
				assertKeys(t, "getCredentials query", queryKeys(t, r.RawQuery), tc.wantKeys)
				if tc.wantRaw != "" && r.RawQuery != tc.wantRaw {
					t.Errorf("request %d raw query = %q, want %q", i+1, r.RawQuery, tc.wantRaw)
				}
				if strings.Contains(r.RawQuery, "=&") || strings.HasSuffix(r.RawQuery, "=") {
					t.Errorf("request %d raw query %q sends an empty parameter value", i+1, r.RawQuery)
				}
			}
		})
	}
}

func TestMockServesOnlyTheContractOperations(t *testing.T) {
	srv := newServer(t, 1000)
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

	authorized := map[string]string{"Authorization": "Bearer " + initialToken}

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
			method:     http.MethodPatch,
			target:     "/v1/credentials",
			body:       strings.NewReader(`{}`),
			header:     map[string]string{"Authorization": "Bearer " + initialToken, "Content-Type": "application/json"},
			wantStatus: http.StatusNotFound,
		},
		{
			name:       "refresh token invalidation is outside the contract",
			method:     http.MethodDelete,
			target:     "/v1/tokens/refresh-token",
			body:       strings.NewReader(`"` + refreshTokenID + `"`),
			header:     map[string]string{"Content-Type": "application/json"},
			wantStatus: http.StatusNotFound,
		},
		{
			name:       "query parameter outside the contract",
			method:     http.MethodGet,
			target:     "/v1/credentials?pageNumber=0&resourceGroup=all",
			header:     authorized,
			wantStatus: http.StatusBadRequest,
			wantOpID:   "getCredentials",
		},
		{
			name:       "optional parameter sent empty",
			method:     http.MethodGet,
			target:     "/v1/credentials?pageNumber=0&domainName=",
			header:     authorized,
			wantStatus: http.StatusBadRequest,
			wantOpID:   "getCredentials",
		},
		{
			name:       "missing bearer token",
			method:     http.MethodGet,
			target:     "/v1/credentials?pageNumber=0",
			wantStatus: http.StatusUnauthorized,
			wantOpID:   "getCredentials",
		},
		{
			name:       "unknown refresh token id",
			method:     http.MethodPatch,
			target:     "/v1/tokens/access-token/refresh",
			body:       strings.NewReader(`"not-the-refresh-token"`),
			header:     map[string]string{"Content-Type": "application/json"},
			wantStatus: http.StatusNotFound,
			wantOpID:   "refreshAccessToken",
		},
		{
			name:       "refresh body sent as an object",
			method:     http.MethodPatch,
			target:     "/v1/tokens/access-token/refresh",
			body:       strings.NewReader(`{"id":"` + refreshTokenID + `"}`),
			header:     map[string]string{"Content-Type": "application/json"},
			wantStatus: http.StatusBadRequest,
			wantOpID:   "refreshAccessToken",
		},
		{
			name:       "wrong credentials",
			method:     http.MethodPost,
			target:     "/v1/tokens",
			body:       strings.NewReader(`{"username":"` + testUsername + `","password":"wrong"}`),
			header:     map[string]string{"Content-Type": "application/json"},
			wantStatus: http.StatusBadRequest,
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

func TestConstructorsRejectMissingRequiredConfiguration(t *testing.T) {
	clientCases := []struct {
		name string
		cfg  client.Config
	}{
		{"base URL", client.Config{Username: "u", Password: "p"}},
		{"username", client.Config{BaseURL: "http://127.0.0.1:1", Password: "p"}},
		{"password", client.Config{BaseURL: "http://127.0.0.1:1", Username: "u"}},
	}
	for _, tc := range clientCases {
		t.Run("client missing "+tc.name, func(t *testing.T) {
			if _, err := client.New(tc.cfg); err == nil {
				t.Fatal("New returned nil error")
			}
		})
	}

	validMock := mock.Config{
		ContractPath:   contractPath,
		FixturePath:    "../testdata/credentials.json",
		AccessToken:    initialToken,
		RefreshedToken: refreshedToken,
		RefreshTokenID: refreshTokenID,
	}
	missingDir := t.TempDir()
	mockCases := []struct {
		name   string
		mutate func(*mock.Config)
	}{
		{"contract path", func(c *mock.Config) { c.ContractPath = "" }},
		{"fixture path", func(c *mock.Config) { c.FixturePath = "" }},
		{"access token", func(c *mock.Config) { c.AccessToken = "" }},
		{"refreshed token", func(c *mock.Config) { c.RefreshedToken = "" }},
		{"refresh token id", func(c *mock.Config) { c.RefreshTokenID = "" }},
		{"missing contract file", func(c *mock.Config) { c.ContractPath = filepath.Join(missingDir, "contract.json") }},
		{"missing fixture file", func(c *mock.Config) { c.FixturePath = filepath.Join(missingDir, "fixture.json") }},
	}
	for _, tc := range mockCases {
		t.Run("mock missing "+tc.name, func(t *testing.T) {
			cfg := validMock
			tc.mutate(&cfg)
			srv, err := mock.New(cfg)
			if err == nil {
				srv.Close()
				t.Fatal("New returned nil error")
			}
		})
	}
}

func TestMockRejectsMalformedRequestsWithErrorResponses(t *testing.T) {
	cases := []struct {
		name   string
		method string
		target string
		body   string
		header map[string]string
		status int
	}{
		{
			name:   "createToken content type differs from the contract",
			method: http.MethodPost,
			target: "/v1/tokens",
			body:   `{"username":"` + testUsername + `","password":"` + testPassword + `"}`,
			header: map[string]string{"Content-Type": "text/plain"},
			status: http.StatusBadRequest,
		},
		{
			name:   "createToken body has a field outside the contract",
			method: http.MethodPost,
			target: "/v1/tokens",
			body:   `{"username":"` + testUsername + `","password":"` + testPassword + `","tenant":"extra"}`,
			header: map[string]string{"Content-Type": "application/json"},
			status: http.StatusBadRequest,
		},
		{
			name:   "refreshAccessToken content type differs from the contract",
			method: http.MethodPatch,
			target: "/v1/tokens/access-token/refresh",
			body:   `"` + refreshTokenID + `"`,
			header: map[string]string{"Content-Type": "text/plain"},
			status: http.StatusBadRequest,
		},
		{
			name:   "malformed bearer token",
			method: http.MethodGet,
			target: "/v1/credentials?pageNumber=0",
			header: map[string]string{"Authorization": "Bearer"},
			status: http.StatusUnauthorized,
		},
		{
			name:   "repeated query parameter",
			method: http.MethodGet,
			target: "/v1/credentials?pageNumber=0&pageNumber=1",
			header: map[string]string{"Authorization": "Bearer " + initialToken},
			status: http.StatusBadRequest,
		},
		{
			name:   "non-numeric page number",
			method: http.MethodGet,
			target: "/v1/credentials?pageNumber=first",
			header: map[string]string{"Authorization": "Bearer " + initialToken},
			status: http.StatusBadRequest,
		},
		{
			name:   "negative page size",
			method: http.MethodGet,
			target: "/v1/credentials?pageNumber=0&pageSize=-1",
			header: map[string]string{"Authorization": "Bearer " + initialToken},
			status: http.StatusBadRequest,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv := newServer(t, 1000)
			var body io.Reader
			if tc.body != "" {
				body = strings.NewReader(tc.body)
			}
			req, err := http.NewRequest(tc.method, srv.URL()+tc.target, body)
			if err != nil {
				t.Fatalf("build request: %v", err)
			}
			for name, value := range tc.header {
				req.Header.Set(name, value)
			}
			resp, err := (&http.Client{Timeout: 5 * time.Second}).Do(req)
			if err != nil {
				t.Fatalf("request: %v", err)
			}
			defer resp.Body.Close()
			if resp.StatusCode != tc.status {
				t.Fatalf("status = %d, want %d", resp.StatusCode, tc.status)
			}
			if got := resp.Header.Get("Content-Type"); got != "application/json" {
				t.Errorf("Content-Type = %q, want application/json", got)
			}
			var apiError map[string]json.RawMessage
			if err := json.NewDecoder(resp.Body).Decode(&apiError); err != nil {
				t.Fatalf("decode Error response: %v", err)
			}
			if apiError == nil {
				t.Error("Error response must be a JSON object")
			}
			allowedErrorFields := map[string]bool{
				"errorCode": true, "errorType": true, "arguments": true, "context": true,
				"message": true, "remediationMessage": true, "causes": true, "nestedErrors": true,
				"referenceToken": true, "label": true, "remediationUrl": true,
			}
			for field := range apiError {
				if !allowedErrorFields[field] {
					t.Errorf("Error response has field %q outside the Error schema", field)
				}
			}
		})
	}
}

func TestClientStopsAfterOneFailedRefreshOrRefusedRetry(t *testing.T) {
	cases := []struct {
		name         string
		refreshFails bool
		wantPages    int64
	}{
		{"refresh fails", true, 1},
		{"refreshed page is refused", false, 2},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var tokenCalls atomic.Int64
			var refreshCalls atomic.Int64
			var pageCalls atomic.Int64
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				switch {
				case r.Method == http.MethodPost && r.URL.Path == "/v1/tokens":
					tokenCalls.Add(1)
					w.WriteHeader(http.StatusCreated)
					_ = json.NewEncoder(w).Encode(map[string]any{
						"accessToken":  initialToken,
						"refreshToken": map[string]string{"id": refreshTokenID},
					})
				case r.Method == http.MethodPatch && r.URL.Path == "/v1/tokens/access-token/refresh":
					refreshCalls.Add(1)
					if tc.refreshFails {
						w.WriteHeader(http.StatusInternalServerError)
						_ = json.NewEncoder(w).Encode(map[string]string{"errorCode": "REFRESH_FAILED", "message": "refresh failed"})
						return
					}
					_ = json.NewEncoder(w).Encode(refreshedToken)
				case r.Method == http.MethodGet && r.URL.Path == "/v1/credentials":
					pageCalls.Add(1)
					w.WriteHeader(http.StatusUnauthorized)
					_ = json.NewEncoder(w).Encode(map[string]string{"errorCode": "TOKEN_EXPIRED", "message": "expired"})
				default:
					w.WriteHeader(http.StatusNotFound)
				}
			}))
			defer srv.Close()

			c, err := client.New(client.Config{BaseURL: srv.URL, Username: testUsername, Password: testPassword})
			if err != nil {
				t.Fatalf("new client: %v", err)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			if _, err := c.ListCredentials(ctx, client.Filter{PageSize: 2}); err == nil {
				t.Fatal("ListCredentials returned nil error")
			}
			if got := tokenCalls.Load(); got != 1 {
				t.Errorf("createToken calls = %d, want 1", got)
			}
			if got := refreshCalls.Load(); got != 1 {
				t.Errorf("refreshAccessToken calls = %d, want 1", got)
			}
			if got := pageCalls.Load(); got != tc.wantPages {
				t.Errorf("getCredentials calls = %d, want %d", got, tc.wantPages)
			}
		})
	}
}

func TestRequestLogIsASnapshot(t *testing.T) {
	srv := newServer(t, 1000)
	c := newClient(t, srv)

	if _, err := c.ListCredentials(context.Background(), client.Filter{PageSize: 5}); err != nil {
		t.Fatalf("ListCredentials: %v", err)
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
	first[0].Header.Set("Content-Type", "text/plain")

	second := srv.Requests()
	if second[0].OperationID == "tampered" {
		t.Error("Requests must return a snapshot the caller cannot mutate")
	}
	if string(second[0].Body) != originalBody {
		t.Error("Requests must return request bodies the caller cannot mutate")
	}
	if second[0].Header.Get("Content-Type") != "application/json" {
		t.Error("Requests must return request headers the caller cannot mutate")
	}
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
