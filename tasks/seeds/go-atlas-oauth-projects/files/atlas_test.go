// Acceptance tests for the atlasadmin package (MongoDB Atlas Administration
// API v2, service-account OAuth). Two loopback hosts play the token endpoint
// and the API host so the tests can prove the credential separation the task
// demands; a third loopback host records whether the client can be lured
// off the API host by a poisoned pagination link. No real Atlas, no real
// credentials, no wall-clock dependence — the clock is injected.
//
// Protected — do not modify this file or anything under docs/.
package atlasadmin

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"
)

const (
	fixClientID     = "mdb_sa_id_fixture0123456789abcdef"
	fixClientSecret = "mdb_sa_sk_fixture_S3CRETdeadbeef42"
	fixToken1       = "eyJfixture.access.token-ONE"
	fixToken2       = "eyJfixture.access.token-TWO"
	acceptGroups    = "application/vnd.atlas.2023-01-01+json"
)

var fixEpoch = time.Date(2026, 7, 17, 12, 0, 0, 0, time.UTC)

type tokenRec struct {
	Method  string
	Path    string
	Auth    string
	CType   string
	Accept  string
	Body    string
}

// tokenServer serves the OAuth token endpoint at /api/oauth/token and
// records every request it sees.
type tokenServer struct {
	mu       sync.Mutex
	reqs     []tokenRec
	respond  func(n int, w http.ResponseWriter) // n = 1-based request count
	srv      *httptest.Server
}

func newTokenServer(t *testing.T, respond func(n int, w http.ResponseWriter)) *tokenServer {
	t.Helper()
	ts := &tokenServer{respond: respond}
	ts.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		ts.mu.Lock()
		ts.reqs = append(ts.reqs, tokenRec{
			Method: r.Method,
			Path:   r.URL.Path,
			Auth:   r.Header.Get("Authorization"),
			CType:  r.Header.Get("Content-Type"),
			Accept: r.Header.Get("Accept"),
			Body:   string(body),
		})
		n := len(ts.reqs)
		ts.mu.Unlock()
		ts.respond(n, w)
	}))
	t.Cleanup(ts.srv.Close)
	return ts
}

func (ts *tokenServer) url() string { return ts.srv.URL + "/api/oauth/token" }

func (ts *tokenServer) count() int {
	ts.mu.Lock()
	defer ts.mu.Unlock()
	return len(ts.reqs)
}

func (ts *tokenServer) req(i int) tokenRec {
	ts.mu.Lock()
	defer ts.mu.Unlock()
	return ts.reqs[i]
}

func grantToken(w http.ResponseWriter, tok string) {
	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"access_token":%q,"expires_in":3600,"token_type":"Bearer"}`, tok)
}

type apiRec struct {
	Method string
	URI    string // path?query as received
	Auth   string
	Accept string
}

// apiServer serves scripted responses per request index and records every
// request.
type apiServer struct {
	mu      sync.Mutex
	reqs    []apiRec
	respond func(n int, r *http.Request, w http.ResponseWriter)
	srv     *httptest.Server
}

func newAPIServer(t *testing.T, respond func(n int, r *http.Request, w http.ResponseWriter)) *apiServer {
	t.Helper()
	as := &apiServer{respond: respond}
	as.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		as.mu.Lock()
		as.reqs = append(as.reqs, apiRec{
			Method: r.Method,
			URI:    r.URL.RequestURI(),
			Auth:   r.Header.Get("Authorization"),
			Accept: r.Header.Get("Accept"),
		})
		n := len(as.reqs)
		as.mu.Unlock()
		as.respond(n, r, w)
	}))
	t.Cleanup(as.srv.Close)
	return as
}

func (as *apiServer) count() int {
	as.mu.Lock()
	defer as.mu.Unlock()
	return len(as.reqs)
}

func (as *apiServer) req(i int) apiRec {
	as.mu.Lock()
	defer as.mu.Unlock()
	return as.reqs[i]
}

func groupsPage(results []string, links []string) string {
	return fmt.Sprintf(`{"links":[%s],"results":[%s],"totalCount":4}`,
		strings.Join(links, ","), strings.Join(results, ","))
}

func link(rel, href string) string {
	return fmt.Sprintf(`{"href":%q,"rel":%q}`, href, rel)
}

func project(id, name, orgID, created string, clusters int) string {
	return fmt.Sprintf(`{"clusterCount":%d,"created":%q,"id":%q,"links":[],"name":%q,"orgId":%q}`,
		clusters, created, id, name, orgID)
}

func newClient(t *testing.T, ts *tokenServer, api string, now func() time.Time) *Client {
	t.Helper()
	c, err := New(Config{
		TokenURL:     ts.url(),
		BaseURL:      api,
		ClientID:     fixClientID,
		ClientSecret: fixClientSecret,
		Now:          now,
	})
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return c
}

func TestConfigValidation(t *testing.T) {
	base := Config{TokenURL: "http://t.invalid/api/oauth/token", BaseURL: "http://a.invalid",
		ClientID: "id", ClientSecret: "sk"}
	for name, mutate := range map[string]func(*Config){
		"empty TokenURL":     func(c *Config) { c.TokenURL = "" },
		"empty BaseURL":      func(c *Config) { c.BaseURL = "" },
		"empty ClientID":     func(c *Config) { c.ClientID = "" },
		"empty ClientSecret": func(c *Config) { c.ClientSecret = "" },
	} {
		cfg := base
		mutate(&cfg)
		if _, err := New(cfg); err == nil {
			t.Errorf("New accepted config with %s", name)
		}
	}
	if _, err := New(base); err != nil {
		t.Errorf("New rejected a complete config: %v", err)
	}
}

func TestTokenExchangeWireFormat(t *testing.T) {
	ts := newTokenServer(t, func(n int, w http.ResponseWriter) { grantToken(w, fixToken1) })
	api := newAPIServer(t, func(n int, r *http.Request, w http.ResponseWriter) {
		w.Header().Set("Content-Type", acceptGroups)
		io.WriteString(w, groupsPage(nil, []string{link("self", "ignored")}))
	})
	c := newClient(t, ts, api.srv.URL, func() time.Time { return fixEpoch })

	if _, err := c.Projects(context.Background()); err != nil {
		t.Fatalf("Projects: %v", err)
	}
	if got := ts.count(); got != 1 {
		t.Fatalf("token endpoint saw %d requests, want 1", got)
	}
	tr := ts.req(0)
	if tr.Method != http.MethodPost {
		t.Errorf("token request method = %q, want POST", tr.Method)
	}
	if tr.Path != "/api/oauth/token" {
		t.Errorf("token request path = %q, want /api/oauth/token", tr.Path)
	}
	wantAuth := "Basic " + base64.StdEncoding.EncodeToString([]byte(fixClientID+":"+fixClientSecret))
	if tr.Auth != wantAuth {
		t.Errorf("token request Authorization = %q, want Basic base64(clientID:clientSecret)", tr.Auth)
	}
	if ct := strings.ToLower(tr.CType); !strings.HasPrefix(ct, "application/x-www-form-urlencoded") {
		t.Errorf("token request Content-Type = %q, want application/x-www-form-urlencoded", tr.CType)
	}
	if !strings.HasPrefix(tr.Accept, "application/json") {
		t.Errorf("token request Accept = %q, want application/json", tr.Accept)
	}
	if tr.Body != "grant_type=client_credentials" {
		t.Errorf("token request body = %q, want grant_type=client_credentials", tr.Body)
	}
}

func TestProjectsRequestAndDecode(t *testing.T) {
	ts := newTokenServer(t, func(n int, w http.ResponseWriter) { grantToken(w, fixToken1) })
	api := newAPIServer(t, func(n int, r *http.Request, w http.ResponseWriter) {
		w.Header().Set("Content-Type", acceptGroups)
		io.WriteString(w, groupsPage(
			[]string{
				project("64f1a1b2c3d4e5f6a7b8c9d0", "payments-prod", "64aa11bb22cc33dd44ee55ff", "2025-11-04T08:15:00Z", 3),
				project("64f1a1b2c3d4e5f6a7b8c9d1", "payments-staging", "64aa11bb22cc33dd44ee55ff", "2026-01-19T17:30:00Z", 0),
			},
			[]string{link("self", "ignored")},
		))
	})
	c := newClient(t, ts, api.srv.URL, func() time.Time { return fixEpoch })

	got, err := c.Projects(context.Background())
	if err != nil {
		t.Fatalf("Projects: %v", err)
	}
	ar := api.req(0)
	if ar.Method != http.MethodGet {
		t.Errorf("groups request method = %q, want GET", ar.Method)
	}
	if !strings.HasPrefix(ar.URI, "/api/atlas/v2/groups") {
		t.Errorf("groups request URI = %q, want /api/atlas/v2/groups", ar.URI)
	}
	if ar.Accept != acceptGroups {
		t.Errorf("groups request Accept = %q, want %q (dated Atlas media type)", ar.Accept, acceptGroups)
	}
	if ar.Auth != "Bearer "+fixToken1 {
		t.Errorf("groups request Authorization = %q, want Bearer %s", ar.Auth, fixToken1)
	}
	want := []Project{
		{ID: "64f1a1b2c3d4e5f6a7b8c9d0", Name: "payments-prod", OrgID: "64aa11bb22cc33dd44ee55ff", Created: "2025-11-04T08:15:00Z", ClusterCount: 3},
		{ID: "64f1a1b2c3d4e5f6a7b8c9d1", Name: "payments-staging", OrgID: "64aa11bb22cc33dd44ee55ff", Created: "2026-01-19T17:30:00Z", ClusterCount: 0},
	}
	if len(got) != len(want) {
		t.Fatalf("Projects returned %d projects, want %d", len(got), len(want))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("project[%d] = %+v, want %+v", i, got[i], want[i])
		}
	}
}

func TestPaginationFollowsNextLinks(t *testing.T) {
	ts := newTokenServer(t, func(n int, w http.ResponseWriter) { grantToken(w, fixToken1) })
	var api *apiServer
	api = newAPIServer(t, func(n int, r *http.Request, w http.ResponseWriter) {
		w.Header().Set("Content-Type", acceptGroups)
		base := api.srv.URL + "/api/atlas/v2/groups"
		switch n {
		case 1:
			io.WriteString(w, groupsPage(
				[]string{
					project("000000000000000000000001", "alpha", "64aa11bb22cc33dd44ee55ff", "2025-01-01T00:00:00Z", 1),
					project("000000000000000000000002", "bravo", "64aa11bb22cc33dd44ee55ff", "2025-02-01T00:00:00Z", 2),
				},
				[]string{link("self", base+"?pageNum=1&itemsPerPage=100"), link("next", base+"?pageNum=2&itemsPerPage=100")},
			))
		case 2:
			io.WriteString(w, groupsPage(
				[]string{project("000000000000000000000003", "charlie", "64aa11bb22cc33dd44ee55ff", "2025-03-01T00:00:00Z", 0)},
				[]string{
					link("previous", base+"?pageNum=1&itemsPerPage=100"),
					link("self", base+"?pageNum=2&itemsPerPage=100"),
					link("next", base+"?pageNum=3&itemsPerPage=100"),
				},
			))
		default:
			io.WriteString(w, groupsPage(
				[]string{project("000000000000000000000004", "delta", "64aa11bb22cc33dd44ee55ff", "2025-04-01T00:00:00Z", 7)},
				[]string{
					link("previous", base+"?pageNum=2&itemsPerPage=100"),
					link("self", base+"?pageNum=3&itemsPerPage=100"),
				},
			))
		}
	})
	c := newClient(t, ts, api.srv.URL, func() time.Time { return fixEpoch })

	got, err := c.Projects(context.Background())
	if err != nil {
		t.Fatalf("Projects: %v", err)
	}
	if api.count() != 3 {
		t.Fatalf("API host saw %d requests, want 3 (one per page)", api.count())
	}
	if uri := api.req(1).URI; uri != "/api/atlas/v2/groups?pageNum=2&itemsPerPage=100" {
		t.Errorf("second request URI = %q, want the rel=next href verbatim", uri)
	}
	if uri := api.req(2).URI; uri != "/api/atlas/v2/groups?pageNum=3&itemsPerPage=100" {
		t.Errorf("third request URI = %q, want the rel=next href verbatim", uri)
	}
	names := make([]string, len(got))
	for i, p := range got {
		names[i] = p.Name
	}
	if strings.Join(names, ",") != "alpha,bravo,charlie,delta" {
		t.Errorf("projects order = %v, want alpha,bravo,charlie,delta", names)
	}
	// One token exchange covered all three pages.
	if ts.count() != 1 {
		t.Errorf("token endpoint saw %d requests across pagination, want 1 (cached)", ts.count())
	}
	// The Bearer token must accompany every page request.
	for i := 0; i < api.count(); i++ {
		if api.req(i).Auth != "Bearer "+fixToken1 {
			t.Errorf("page request %d Authorization = %q, want Bearer %s", i+1, api.req(i).Auth, fixToken1)
		}
	}
}

func TestEmptyResults(t *testing.T) {
	ts := newTokenServer(t, func(n int, w http.ResponseWriter) { grantToken(w, fixToken1) })
	api := newAPIServer(t, func(n int, r *http.Request, w http.ResponseWriter) {
		w.Header().Set("Content-Type", acceptGroups)
		io.WriteString(w, `{"links":[{"href":"ignored","rel":"self"}],"results":[],"totalCount":0}`)
	})
	c := newClient(t, ts, api.srv.URL, func() time.Time { return fixEpoch })

	got, err := c.Projects(context.Background())
	if err != nil {
		t.Fatalf("Projects on empty list: %v", err)
	}
	if len(got) != 0 {
		t.Errorf("Projects returned %d items for an empty result set, want 0", len(got))
	}
}

func TestTokenCachedAcrossCallsAndRefreshedOnExpiry(t *testing.T) {
	ts := newTokenServer(t, func(n int, w http.ResponseWriter) {
		if n == 1 {
			grantToken(w, fixToken1)
		} else {
			grantToken(w, fixToken2)
		}
	})
	api := newAPIServer(t, func(n int, r *http.Request, w http.ResponseWriter) {
		w.Header().Set("Content-Type", acceptGroups)
		io.WriteString(w, groupsPage(nil, []string{link("self", "ignored")}))
	})
	now := fixEpoch
	var mu sync.Mutex
	clock := func() time.Time { mu.Lock(); defer mu.Unlock(); return now }
	set := func(t2 time.Time) { mu.Lock(); now = t2; mu.Unlock() }

	c := newClient(t, ts, api.srv.URL, clock)
	ctx := context.Background()

	if _, err := c.Projects(ctx); err != nil {
		t.Fatalf("Projects #1: %v", err)
	}
	// 3000s later: token still comfortably valid — must be reused.
	set(fixEpoch.Add(3000 * time.Second))
	if _, err := c.Projects(ctx); err != nil {
		t.Fatalf("Projects #2: %v", err)
	}
	if ts.count() != 1 {
		t.Fatalf("token endpoint saw %d requests after two calls inside the token lifetime, want 1", ts.count())
	}
	if api.req(1).Auth != "Bearer "+fixToken1 {
		t.Errorf("second call Authorization = %q, want cached Bearer %s", api.req(1).Auth, fixToken1)
	}
	// 3540s after issuance the token is inside the 60s early-refresh margin
	// of its 3600s lifetime — the client must exchange again.
	set(fixEpoch.Add(3540 * time.Second))
	if _, err := c.Projects(ctx); err != nil {
		t.Fatalf("Projects #3: %v", err)
	}
	if ts.count() != 2 {
		t.Fatalf("token endpoint saw %d requests after expiry margin, want 2", ts.count())
	}
	if api.req(2).Auth != "Bearer "+fixToken2 {
		t.Errorf("post-refresh Authorization = %q, want Bearer %s", api.req(2).Auth, fixToken2)
	}
}

func TestConcurrentCallsSingleTokenExchange(t *testing.T) {
	ts := newTokenServer(t, func(n int, w http.ResponseWriter) { grantToken(w, fixToken1) })
	api := newAPIServer(t, func(n int, r *http.Request, w http.ResponseWriter) {
		w.Header().Set("Content-Type", acceptGroups)
		io.WriteString(w, groupsPage(nil, []string{link("self", "ignored")}))
	})
	c := newClient(t, ts, api.srv.URL, func() time.Time { return fixEpoch })

	const workers = 8
	var wg sync.WaitGroup
	errs := make([]error, workers)
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			_, errs[i] = c.Projects(context.Background())
		}(i)
	}
	wg.Wait()
	for i, err := range errs {
		if err != nil {
			t.Fatalf("concurrent Projects[%d]: %v", i, err)
		}
	}
	if api.count() != workers {
		t.Errorf("API host saw %d requests, want %d", api.count(), workers)
	}
	if ts.count() != 1 {
		t.Errorf("token endpoint saw %d requests from %d concurrent calls, want 1 (single-flight)", ts.count(), workers)
	}
}

func TestUnauthorizedTriggersOneRefreshThenFails(t *testing.T) {
	apiError := func(w http.ResponseWriter, status int, code, detail, reason string) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(status)
		fmt.Fprintf(w, `{"detail":%q,"error":%d,"errorCode":%q,"parameters":[],"reason":%q}`,
			detail, status, code, reason)
	}

	t.Run("recovers after refresh", func(t *testing.T) {
		ts := newTokenServer(t, func(n int, w http.ResponseWriter) {
			if n == 1 {
				grantToken(w, fixToken1)
			} else {
				grantToken(w, fixToken2)
			}
		})
		api := newAPIServer(t, func(n int, r *http.Request, w http.ResponseWriter) {
			if r.Header.Get("Authorization") == "Bearer "+fixToken2 {
				w.Header().Set("Content-Type", acceptGroups)
				io.WriteString(w, groupsPage(nil, []string{link("self", "ignored")}))
				return
			}
			apiError(w, http.StatusUnauthorized, "INVALID_AUTHORIZATION_HEADER", "Token is expired.", "Unauthorized")
		})
		c := newClient(t, ts, api.srv.URL, func() time.Time { return fixEpoch })
		if _, err := c.Projects(context.Background()); err != nil {
			t.Fatalf("Projects should succeed after one re-exchange, got: %v", err)
		}
		if ts.count() != 2 {
			t.Errorf("token endpoint saw %d requests, want 2 (initial + one refresh after 401)", ts.count())
		}
		if api.count() != 2 {
			t.Errorf("API host saw %d requests, want 2 (401 then retried once)", api.count())
		}
	})

	t.Run("persistent 401 surfaces APIError", func(t *testing.T) {
		ts := newTokenServer(t, func(n int, w http.ResponseWriter) { grantToken(w, fixToken1) })
		api := newAPIServer(t, func(n int, r *http.Request, w http.ResponseWriter) {
			apiError(w, http.StatusUnauthorized, "INVALID_AUTHORIZATION_HEADER", "Token is expired.", "Unauthorized")
		})
		c := newClient(t, ts, api.srv.URL, func() time.Time { return fixEpoch })
		_, err := c.Projects(context.Background())
		var ae *APIError
		if !errors.As(err, &ae) {
			t.Fatalf("Projects error = %v (%T), want *APIError", err, err)
		}
		if ae.StatusCode != 401 || ae.ErrorCode != "INVALID_AUTHORIZATION_HEADER" {
			t.Errorf("APIError = %+v, want StatusCode 401, ErrorCode INVALID_AUTHORIZATION_HEADER", ae)
		}
		if api.count() != 2 {
			t.Errorf("API host saw %d requests, want exactly 2 (retry once, never loop)", api.count())
		}
	})
}

func TestAPIErrorDecode(t *testing.T) {
	ts := newTokenServer(t, func(n int, w http.ResponseWriter) { grantToken(w, fixToken1) })
	api := newAPIServer(t, func(n int, r *http.Request, w http.ResponseWriter) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		io.WriteString(w, `{"detail":"Cannot find resource /api/atlas/v2/groups.","error":404,"errorCode":"RESOURCE_NOT_FOUND","parameters":["/api/atlas/v2/groups"],"reason":"Not Found"}`)
	})
	c := newClient(t, ts, api.srv.URL, func() time.Time { return fixEpoch })

	_, err := c.Projects(context.Background())
	var ae *APIError
	if !errors.As(err, &ae) {
		t.Fatalf("Projects error = %v (%T), want *APIError", err, err)
	}
	if ae.StatusCode != 404 {
		t.Errorf("APIError.StatusCode = %d, want 404", ae.StatusCode)
	}
	if ae.ErrorCode != "RESOURCE_NOT_FOUND" {
		t.Errorf("APIError.ErrorCode = %q, want RESOURCE_NOT_FOUND", ae.ErrorCode)
	}
	if ae.Detail != "Cannot find resource /api/atlas/v2/groups." {
		t.Errorf("APIError.Detail = %q", ae.Detail)
	}
	if ae.Reason != "Not Found" {
		t.Errorf("APIError.Reason = %q, want Not Found", ae.Reason)
	}
	if msg := err.Error(); strings.Contains(msg, fixClientSecret) || strings.Contains(msg, fixToken1) {
		t.Errorf("APIError message leaks credentials: %q", msg)
	}
}

func TestOAuthErrorDecode(t *testing.T) {
	ts := newTokenServer(t, func(n int, w http.ResponseWriter) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		io.WriteString(w, `{"error":"invalid_client","error_description":"Invalid credentials provided."}`)
	})
	api := newAPIServer(t, func(n int, r *http.Request, w http.ResponseWriter) {
		t.Error("API host must not be contacted when the token exchange fails")
	})
	c := newClient(t, ts, api.srv.URL, func() time.Time { return fixEpoch })

	_, err := c.Projects(context.Background())
	var te *TokenError
	if !errors.As(err, &te) {
		t.Fatalf("Projects error = %v (%T), want *TokenError", err, err)
	}
	if te.Code != "invalid_client" {
		t.Errorf("TokenError.Code = %q, want invalid_client", te.Code)
	}
	if te.Description != "Invalid credentials provided." {
		t.Errorf("TokenError.Description = %q", te.Description)
	}
	if msg := err.Error(); strings.Contains(msg, fixClientSecret) {
		t.Errorf("TokenError message leaks the client secret: %q", msg)
	}
	if api.count() != 0 {
		t.Errorf("API host saw %d requests despite failed token exchange, want 0", api.count())
	}
}

func TestCredentialSeparationBetweenHosts(t *testing.T) {
	ts := newTokenServer(t, func(n int, w http.ResponseWriter) { grantToken(w, fixToken1) })
	api := newAPIServer(t, func(n int, r *http.Request, w http.ResponseWriter) {
		w.Header().Set("Content-Type", acceptGroups)
		io.WriteString(w, groupsPage(nil, []string{link("self", "ignored")}))
	})
	c := newClient(t, ts, api.srv.URL, func() time.Time { return fixEpoch })
	if _, err := c.Projects(context.Background()); err != nil {
		t.Fatalf("Projects: %v", err)
	}
	if auth := ts.req(0).Auth; strings.HasPrefix(auth, "Bearer ") {
		t.Errorf("token endpoint received a Bearer token (%q); it must only ever see Basic credentials", auth)
	}
	if auth := api.req(0).Auth; strings.HasPrefix(auth, "Basic ") {
		t.Errorf("API host received Basic credentials (%q); the client secret must never reach the API host", auth)
	}
}

func TestCrossHostNextLinkRefused(t *testing.T) {
	var evilHits int
	var evilMu sync.Mutex
	evil := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		evilMu.Lock()
		evilHits++
		evilMu.Unlock()
		io.WriteString(w, `{"links":[],"results":[]}`)
	}))
	defer evil.Close()

	ts := newTokenServer(t, func(n int, w http.ResponseWriter) { grantToken(w, fixToken1) })
	api := newAPIServer(t, func(n int, r *http.Request, w http.ResponseWriter) {
		w.Header().Set("Content-Type", acceptGroups)
		io.WriteString(w, groupsPage(
			[]string{project("000000000000000000000009", "echo", "64aa11bb22cc33dd44ee55ff", "2025-05-01T00:00:00Z", 0)},
			[]string{link("self", "ignored"), link("next", evil.URL+"/api/atlas/v2/groups?pageNum=2")},
		))
	})
	c := newClient(t, ts, api.srv.URL, func() time.Time { return fixEpoch })

	_, err := c.Projects(context.Background())
	if err == nil {
		t.Fatal("Projects followed a pagination link onto a different host without error")
	}
	evilMu.Lock()
	hits := evilHits
	evilMu.Unlock()
	if hits != 0 {
		t.Errorf("the off-host link target was contacted %d times; credentials must never leave the API host", hits)
	}
	if msg := err.Error(); strings.Contains(msg, fixToken1) || strings.Contains(msg, fixClientSecret) {
		t.Errorf("cross-host error message leaks credentials: %q", msg)
	}
}

func TestContextCancellation(t *testing.T) {
	ts := newTokenServer(t, func(n int, w http.ResponseWriter) { grantToken(w, fixToken1) })
	api := newAPIServer(t, func(n int, r *http.Request, w http.ResponseWriter) {
		w.Header().Set("Content-Type", acceptGroups)
		io.WriteString(w, groupsPage(nil, []string{link("self", "ignored")}))
	})
	c := newClient(t, ts, api.srv.URL, func() time.Time { return fixEpoch })

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := c.Projects(ctx); !errors.Is(err, context.Canceled) {
		t.Errorf("Projects with cancelled context returned %v, want an error wrapping context.Canceled", err)
	}
}

// Guard: the protected contract fixtures must parse and stay aligned with the
// values these tests pin.
func TestContractFixtureIntegrity(t *testing.T) {
	for _, name := range []string{"docs/contract.json", "docs/official_sources.json"} {
		raw, err := os.ReadFile(name)
		if err != nil {
			t.Fatalf("reading %s: %v", name, err)
		}
		var v map[string]any
		if err := json.Unmarshal(raw, &v); err != nil {
			t.Fatalf("%s is not valid JSON: %v", name, err)
		}
		if name == "docs/contract.json" {
			if got := v["accept_media_type"]; got != acceptGroups {
				t.Errorf("contract.json accept_media_type = %v, want %q", got, acceptGroups)
			}
		}
	}
}
