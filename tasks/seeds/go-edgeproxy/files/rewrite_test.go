package proxy

import (
	"crypto/tls"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
)

func mustURL(t *testing.T, raw string) *url.URL {
	t.Helper()
	u, err := url.Parse(raw)
	if err != nil {
		t.Fatalf("parse %q: %v", raw, err)
	}
	return u
}

func inbound(t *testing.T, target string) *http.Request {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, target, nil)
	req.RemoteAddr = "203.0.113.7:44012"
	return req
}

func TestRewriteTargetsUpstream(t *testing.T) {
	up := mustURL(t, "http://backend.internal:9000")
	in := inbound(t, "http://edge.example/api/users?limit=2&page=3")
	out := RewriteRequest(in, up, "/api")

	if out.URL.Scheme != "http" || out.URL.Host != "backend.internal:9000" {
		t.Fatalf("outbound URL = %s, want the upstream scheme/host", out.URL)
	}
	if out.URL.Path != "/users" {
		t.Fatalf("outbound path = %q, want /users (prefix /api stripped)", out.URL.Path)
	}
	if out.URL.RawQuery != "limit=2&page=3" {
		t.Fatalf("query must survive untouched, got %q", out.URL.RawQuery)
	}
	if out.Host != "backend.internal:9000" {
		t.Fatalf("outbound Host header = %q, want the upstream host", out.Host)
	}
	if out.Method != http.MethodGet {
		t.Fatalf("method = %q", out.Method)
	}
	if out.RequestURI != "" {
		t.Fatal("outbound request must have RequestURI cleared or the transport will refuse it")
	}
}

func TestRewritePrefixStripping(t *testing.T) {
	cases := []struct {
		name         string
		upstream     string
		strip        string
		inPath       string
		wantPath     string
	}{
		{"strips on segment boundary", "http://b:9000", "/api", "/api/users", "/users"},
		{"prefix alone becomes root", "http://b:9000", "/api", "/api", "/"},
		{"trailing slash after prefix", "http://b:9000", "/api", "/api/", "/"},
		{"not a segment match", "http://b:9000", "/api", "/apiv2/users", "/apiv2/users"},
		{"empty strip keeps path", "http://b:9000", "", "/api/users", "/api/users"},
		{"joins upstream base path", "http://b:9000/internal", "/api", "/api/users", "/internal/users"},
		{"no double slash on join", "http://b:9000/internal/", "/api", "/api/users", "/internal/users"},
		{"base path plus bare prefix", "http://b:9000/internal", "/api", "/api", "/internal/"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			in := inbound(t, "http://edge.example"+tc.inPath)
			out := RewriteRequest(in, mustURL(t, tc.upstream), tc.strip)
			if out.URL.Path != tc.wantPath {
				t.Fatalf("path %q with strip %q against %s -> %q, want %q",
					tc.inPath, tc.strip, tc.upstream, out.URL.Path, tc.wantPath)
			}
		})
	}
}

func TestRewriteForwardedHeaders(t *testing.T) {
	up := mustURL(t, "http://b:9000")

	t.Run("first hop sets everything", func(t *testing.T) {
		in := inbound(t, "http://edge.example/api/x")
		out := RewriteRequest(in, up, "/api")
		if got := out.Header.Get("X-Forwarded-For"); got != "203.0.113.7" {
			t.Fatalf("X-Forwarded-For = %q, want the bare client ip 203.0.113.7", got)
		}
		if got := out.Header.Get("X-Forwarded-Proto"); got != "http" {
			t.Fatalf("X-Forwarded-Proto = %q, want http", got)
		}
		if got := out.Header.Get("X-Forwarded-Host"); got != "edge.example" {
			t.Fatalf("X-Forwarded-Host = %q, want the original Host edge.example", got)
		}
	})

	t.Run("existing X-Forwarded-For is appended to", func(t *testing.T) {
		in := inbound(t, "http://edge.example/api/x")
		in.Header.Set("X-Forwarded-For", "198.51.100.1")
		out := RewriteRequest(in, up, "/api")
		if got := out.Header.Get("X-Forwarded-For"); got != "198.51.100.1, 203.0.113.7" {
			t.Fatalf("X-Forwarded-For = %q, want \"198.51.100.1, 203.0.113.7\" (append, comma-space)", got)
		}
	})

	t.Run("existing proto and host are preserved", func(t *testing.T) {
		in := inbound(t, "http://edge.example/api/x")
		in.Header.Set("X-Forwarded-Proto", "https")
		in.Header.Set("X-Forwarded-Host", "public.example")
		out := RewriteRequest(in, up, "/api")
		if got := out.Header.Get("X-Forwarded-Proto"); got != "https" {
			t.Fatalf("X-Forwarded-Proto = %q, an earlier hop's value must win", got)
		}
		if got := out.Header.Get("X-Forwarded-Host"); got != "public.example" {
			t.Fatalf("X-Forwarded-Host = %q, an earlier hop's value must win", got)
		}
	})

	t.Run("tls request means https proto", func(t *testing.T) {
		in := inbound(t, "https://edge.example/api/x")
		in.TLS = &tls.ConnectionState{}
		out := RewriteRequest(in, up, "/api")
		if got := out.Header.Get("X-Forwarded-Proto"); got != "https" {
			t.Fatalf("X-Forwarded-Proto = %q, want https for a TLS request", got)
		}
	})

	t.Run("unparsable remote addr used verbatim", func(t *testing.T) {
		in := inbound(t, "http://edge.example/api/x")
		in.RemoteAddr = "unix-socket-peer"
		out := RewriteRequest(in, up, "/api")
		if got := out.Header.Get("X-Forwarded-For"); got != "unix-socket-peer" {
			t.Fatalf("X-Forwarded-For = %q, want the raw RemoteAddr when it has no port", got)
		}
	})
}

func TestRewriteRemovesHopByHopHeaders(t *testing.T) {
	up := mustURL(t, "http://b:9000")
	in := inbound(t, "http://edge.example/api/x")
	for k, v := range map[string]string{
		"Connection":          "keep-alive, X-Debug-Token",
		"Keep-Alive":          "timeout=5",
		"Proxy-Connection":    "keep-alive",
		"Proxy-Authenticate":  "Basic",
		"Proxy-Authorization": "Basic Zm9vOmJhcg==",
		"Te":                  "trailers",
		"Trailer":             "Expires",
		"Upgrade":             "websocket",
		"X-Debug-Token":       "abc123",
		"Accept":              "application/json",
		"Authorization":       "Bearer tok",
		"X-Tenant":            "acme",
	} {
		in.Header.Set(k, v)
	}
	out := RewriteRequest(in, up, "/api")

	for _, gone := range []string{
		"Connection", "Keep-Alive", "Proxy-Connection", "Proxy-Authenticate",
		"Proxy-Authorization", "Te", "Trailer", "Upgrade",
		// listed in the Connection header, so hop-by-hop for this connection:
		"X-Debug-Token",
	} {
		if v := out.Header.Get(gone); v != "" {
			t.Fatalf("hop-by-hop header %s leaked upstream with value %q", gone, v)
		}
	}
	for k, want := range map[string]string{
		"Accept":        "application/json",
		"Authorization": "Bearer tok",
		"X-Tenant":      "acme",
	} {
		if got := out.Header.Get(k); got != want {
			t.Fatalf("end-to-end header %s = %q, want %q", k, got, want)
		}
	}
}

func TestRewriteDoesNotMutateInbound(t *testing.T) {
	up := mustURL(t, "http://b:9000")
	in := inbound(t, "http://edge.example/api/users?x=1")
	in.Header.Set("Connection", "X-Debug-Token")
	in.Header.Set("X-Debug-Token", "abc")
	in.Header.Set("Accept", "text/plain")

	out := RewriteRequest(in, up, "/api")
	out.Header.Set("X-Added-Downstream", "1")

	if in.URL.Path != "/api/users" || in.Host != "edge.example" {
		t.Fatalf("inbound request mutated: path %q host %q", in.URL.Path, in.Host)
	}
	if in.Header.Get("X-Debug-Token") != "abc" || in.Header.Get("Connection") != "X-Debug-Token" {
		t.Fatal("inbound headers were stripped in place; the rewrite must work on a copy")
	}
	if in.Header.Get("X-Forwarded-For") != "" {
		t.Fatal("X-Forwarded-For bled back into the inbound request")
	}
	if in.Header.Get("X-Added-Downstream") != "" {
		t.Fatal("outbound header map is shared with the inbound request")
	}
}

func TestRewriteKeepsMethodAndBody(t *testing.T) {
	up := mustURL(t, "http://b:9000")
	in := httptest.NewRequest(http.MethodPost, "http://edge.example/api/ingest", strings.NewReader(`{"n":1}`))
	in.RemoteAddr = "203.0.113.7:44012"
	out := RewriteRequest(in, up, "/api")
	if out.Method != http.MethodPost {
		t.Fatalf("method = %q, want POST", out.Method)
	}
	if out.Body == nil {
		t.Fatal("body was dropped by the rewrite")
	}
	got, err := io.ReadAll(out.Body)
	if err != nil || string(got) != `{"n":1}` {
		t.Fatalf("body = %q, %v", got, err)
	}
}

func TestStripHopByHop(t *testing.T) {
	h := http.Header{}
	h.Set("Connection", "close")
	h.Add("Connection", "x-secret-hop, , X-Another")
	h.Set("X-Secret-Hop", "1")
	h.Set("X-Another", "2")
	h.Set("Keep-Alive", "timeout=5")
	h.Set("Transfer-Encoding", "chunked")
	h.Set("X-Keep", "yes")
	h.Set("Content-Type", "text/plain")

	StripHopByHop(h)

	for _, gone := range []string{"Connection", "Keep-Alive", "Transfer-Encoding", "X-Secret-Hop", "X-Another"} {
		if v := h.Get(gone); v != "" {
			t.Fatalf("%s should have been stripped, still %q", gone, v)
		}
	}
	if h.Get("X-Keep") != "yes" || h.Get("Content-Type") != "text/plain" {
		t.Fatalf("end-to-end headers were over-stripped: %v", h)
	}
}
