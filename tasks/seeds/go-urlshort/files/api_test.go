package shortener

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"regexp"
	"strings"
	"testing"
)

var codeRe = regexp.MustCompile(`^[A-Za-z0-9_-]{4,32}$`)

func newTestServer(t *testing.T) (*httptest.Server, *http.Client) {
	t.Helper()
	srv := httptest.NewServer(NewServer())
	t.Cleanup(srv.Close)
	client := &http.Client{
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	return srv, client
}

func doJSON(t *testing.T, client *http.Client, method, url, body string) (*http.Response, map[string]any) {
	t.Helper()
	var rdr io.Reader
	if body != "" {
		rdr = strings.NewReader(body)
	}
	req, err := http.NewRequest(method, url, rdr)
	if err != nil {
		t.Fatal(err)
	}
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	var decoded map[string]any
	// Only bodies declared as JSON are decoded; anything else (e.g. the
	// router's own 405 text) comes back as a nil map.
	if len(raw) > 0 && strings.HasPrefix(resp.Header.Get("Content-Type"), "application/json") {
		if err := json.Unmarshal(raw, &decoded); err != nil {
			t.Fatalf("%s %s: Content-Type says JSON but body is not (%v): %q", method, url, err, raw)
		}
	}
	return resp, decoded
}

func create(t *testing.T, client *http.Client, base, body string) (*http.Response, map[string]any) {
	t.Helper()
	return doJSON(t, client, http.MethodPost, base+"/api/links", body)
}

func TestCreateRedirectStatsRoundTrip(t *testing.T) {
	srv, client := newTestServer(t)
	target := "https://example.com/docs?page=2"

	resp, body := create(t, client, srv.URL, fmt.Sprintf(`{"url":%q}`, target))
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("create status = %d, want 201 (body %v)", resp.StatusCode, body)
	}
	if ct := resp.Header.Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		t.Fatalf("create Content-Type = %q, want application/json", ct)
	}
	code, _ := body["code"].(string)
	if !codeRe.MatchString(code) {
		t.Fatalf("generated code %q does not match [A-Za-z0-9_-]{4,32}", code)
	}
	if body["url"] != target {
		t.Fatalf("create echoed url %v, want %q", body["url"], target)
	}
	if loc := resp.Header.Get("Location"); loc != "/r/"+code {
		t.Fatalf("create Location = %q, want %q", loc, "/r/"+code)
	}

	for i := 0; i < 3; i++ {
		r, err := client.Get(srv.URL + "/r/" + code)
		if err != nil {
			t.Fatal(err)
		}
		io.Copy(io.Discard, r.Body)
		r.Body.Close()
		if r.StatusCode != http.StatusFound {
			t.Fatalf("redirect status = %d, want 302", r.StatusCode)
		}
		if loc := r.Header.Get("Location"); loc != target {
			t.Fatalf("redirect Location = %q, want %q", loc, target)
		}
	}

	resp, stats := doJSON(t, client, http.MethodGet, srv.URL+"/api/links/"+code, "")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("stats status = %d, want 200", resp.StatusCode)
	}
	if stats["code"] != code || stats["url"] != target {
		t.Fatalf("stats identity fields wrong: %v", stats)
	}
	if hits, _ := stats["hits"].(float64); hits != 3 {
		t.Fatalf("stats hits = %v, want 3", stats["hits"])
	}
}

func TestStatsEndpointDoesNotCountAsHit(t *testing.T) {
	srv, client := newTestServer(t)
	_, body := create(t, client, srv.URL, `{"url":"https://internal.wiki/page"}`)
	code := body["code"].(string)

	for i := 0; i < 2; i++ {
		resp, stats := doJSON(t, client, http.MethodGet, srv.URL+"/api/links/"+code, "")
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("stats status = %d", resp.StatusCode)
		}
		if hits, _ := stats["hits"].(float64); hits != 0 {
			t.Fatalf("hits after %d stats reads = %v, want 0 — stats must not increment", i+1, stats["hits"])
		}
	}
}

func TestCustomCodesAndConflicts(t *testing.T) {
	srv, client := newTestServer(t)

	resp, body := create(t, client, srv.URL, `{"url":"https://example.com/a","code":"launch-2026"}`)
	if resp.StatusCode != http.StatusCreated || body["code"] != "launch-2026" {
		t.Fatalf("custom code create: status %d body %v", resp.StatusCode, body)
	}

	resp, body = create(t, client, srv.URL, `{"url":"https://example.com/b","code":"launch-2026"}`)
	if resp.StatusCode != http.StatusConflict {
		t.Fatalf("duplicate custom code: status = %d, want 409", resp.StatusCode)
	}
	if msg, _ := body["error"].(string); msg == "" {
		t.Fatalf("conflict response must carry a JSON error field, got %v", body)
	}

	// The original mapping must be untouched by the failed create.
	r, err := client.Get(srv.URL + "/r/launch-2026")
	if err != nil {
		t.Fatal(err)
	}
	io.Copy(io.Discard, r.Body)
	r.Body.Close()
	if loc := r.Header.Get("Location"); loc != "https://example.com/a" {
		t.Fatalf("after conflict, redirect goes to %q, want the original https://example.com/a", loc)
	}
}

func TestValidationErrors(t *testing.T) {
	srv, client := newTestServer(t)
	cases := []struct {
		name string
		body string
	}{
		{"malformed json", `{nope`},
		{"empty url", `{"url":""}`},
		{"relative url", `{"url":"/docs/page"}`},
		{"no scheme", `{"url":"example.com/x"}`},
		{"ftp scheme", `{"url":"ftp://example.com/x"}`},
		{"code with space", `{"url":"https://ok.example/x","code":"has space"}`},
		{"code with unicode", `{"url":"https://ok.example/x","code":"uniçode"}`},
		{"code too long", fmt.Sprintf(`{"url":"https://ok.example/x","code":%q}`, strings.Repeat("a", 33))},
	}
	for _, tc := range cases {
		resp, body := create(t, client, srv.URL, tc.body)
		if resp.StatusCode != http.StatusBadRequest {
			t.Fatalf("%s: status = %d, want 400", tc.name, resp.StatusCode)
		}
		if msg, _ := body["error"].(string); msg == "" {
			t.Fatalf("%s: 400 responses must be JSON with an error field, got %v", tc.name, body)
		}
	}

	// A maximal valid custom code is accepted.
	resp, body := create(t, client, srv.URL, `{"url":"https://ok.example/y","code":"UPPER_lower-09"}`)
	if resp.StatusCode != http.StatusCreated || body["code"] != "UPPER_lower-09" {
		t.Fatalf("valid custom code rejected: status %d body %v", resp.StatusCode, body)
	}
}

func TestUnknownCodesAndMethods(t *testing.T) {
	srv, client := newTestServer(t)

	r, err := client.Get(srv.URL + "/r/nosuchcode")
	if err != nil {
		t.Fatal(err)
	}
	io.Copy(io.Discard, r.Body)
	r.Body.Close()
	if r.StatusCode != http.StatusNotFound {
		t.Fatalf("unknown redirect: status = %d, want 404", r.StatusCode)
	}

	resp, body := doJSON(t, client, http.MethodGet, srv.URL+"/api/links/nosuchcode", "")
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("unknown stats: status = %d, want 404", resp.StatusCode)
	}
	if msg, _ := body["error"].(string); msg == "" {
		t.Fatalf("404 stats response must be a JSON error, got %v", body)
	}

	resp, _ = doJSON(t, client, http.MethodGet, srv.URL+"/api/links", "")
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Fatalf("GET on the create endpoint: status = %d, want 405", resp.StatusCode)
	}
	resp, _ = doJSON(t, client, http.MethodPatch, srv.URL+"/api/links/somecode", "")
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Fatalf("PATCH on the stats endpoint: status = %d, want 405", resp.StatusCode)
	}
}

func TestSameURLGetsIndependentLinks(t *testing.T) {
	srv, client := newTestServer(t)
	const target = `{"url":"https://example.com/shared"}`
	_, first := create(t, client, srv.URL, target)
	_, second := create(t, client, srv.URL, target)
	c1, _ := first["code"].(string)
	c2, _ := second["code"].(string)
	if c1 == "" || c2 == "" || c1 == c2 {
		t.Fatalf("posting the same URL twice must mint two independent codes, got %q and %q", c1, c2)
	}
}

func TestGeneratedCodesNeverCollide(t *testing.T) {
	srv, client := newTestServer(t)
	seen := map[string]bool{}
	for i := 0; i < 200; i++ {
		resp, body := create(t, client, srv.URL, fmt.Sprintf(`{"url":"https://example.com/page/%d"}`, i))
		if resp.StatusCode != http.StatusCreated {
			t.Fatalf("create %d: status %d", i, resp.StatusCode)
		}
		code, _ := body["code"].(string)
		if !codeRe.MatchString(code) {
			t.Fatalf("create %d: bad code %q", i, code)
		}
		if seen[code] {
			t.Fatalf("create %d: code %q already issued", i, code)
		}
		seen[code] = true
	}
}
