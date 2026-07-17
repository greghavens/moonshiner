package router

import (
	"errors"
	"testing"
)

// Acceptance tests for pattern matching: ":name" captures one segment,
// "*name" (final segment only) captures the non-empty remainder, and
// precedence at each segment is static > param > wildcard, decided
// left to right. Match reports captures via Params.

func buildAPIRouter(t *testing.T) *Router[string] {
	t.Helper()
	r := New[string]()
	for pattern, h := range map[string]string{
		"/":                   "root",
		"/api/users/me":       "me",
		"/api/users/:id":      "user",
		"/api/:section":       "section",
		"/api/:section/stats": "stats",
		"/api/*rest":          "api-catchall",
		"/repos/:owner/:repo": "repo",
		"/files/*path":        "files",
	} {
		if err := r.Handle(pattern, h); err != nil {
			t.Fatalf("Handle(%s): %v", pattern, err)
		}
	}
	return r
}

func mustMatch(t *testing.T, r *Router[string], path, wantHandler string, wantParams map[string]string) {
	t.Helper()
	h, params, ok := r.Match(path)
	if !ok {
		t.Fatalf("Match(%s) = miss, want %q", path, wantHandler)
	}
	if h != wantHandler {
		t.Fatalf("Match(%s) handler = %q, want %q", path, h, wantHandler)
	}
	if len(params) != len(wantParams) {
		t.Fatalf("Match(%s) params = %v, want %v", path, params, wantParams)
	}
	for k, v := range wantParams {
		if params[k] != v {
			t.Fatalf("Match(%s) params[%q] = %q, want %q", path, k, params[k], v)
		}
	}
}

func TestMatchCapturesParams(t *testing.T) {
	r := buildAPIRouter(t)
	mustMatch(t, r, "/api/users/42", "user", map[string]string{"id": "42"})
	mustMatch(t, r, "/repos/anthropic/fable", "repo",
		map[string]string{"owner": "anthropic", "repo": "fable"})
}

func TestMatchStaticBeatsParamBeatsWildcard(t *testing.T) {
	r := buildAPIRouter(t)
	// Exact route wins over ":id" and "*rest".
	mustMatch(t, r, "/api/users/me", "me", nil)
	// Param route wins over the wildcard.
	mustMatch(t, r, "/api/metrics", "section", map[string]string{"section": "metrics"})
	// Precedence is decided left to right: at segment 2, the static
	// "users" of /api/users/:id beats the ":section" of /api/:section/stats.
	mustMatch(t, r, "/api/users/stats", "user", map[string]string{"id": "stats"})
}

func TestMatchWildcardCapturesRemainder(t *testing.T) {
	r := buildAPIRouter(t)
	mustMatch(t, r, "/api/users/42/posts", "api-catchall",
		map[string]string{"rest": "users/42/posts"})
	mustMatch(t, r, "/files/docs/2026/q2.pdf", "files",
		map[string]string{"path": "docs/2026/q2.pdf"})
}

func TestMatchWildcardNeedsAtLeastOneSegment(t *testing.T) {
	r := buildAPIRouter(t)
	if _, _, ok := r.Match("/api"); ok {
		t.Fatal("Match(/api) matched, but *rest requires at least one segment")
	}
	if _, _, ok := r.Match("/files"); ok {
		t.Fatal("Match(/files) matched, but *path requires at least one segment")
	}
}

func TestMatchMisses(t *testing.T) {
	r := buildAPIRouter(t)
	if _, _, ok := r.Match("/repos/anthropic"); ok {
		t.Fatal("Match(/repos/anthropic) matched a two-param route with one segment")
	}
	if _, _, ok := r.Match("/nope"); ok {
		t.Fatal("Match(/nope) matched nothing registered")
	}
}

func TestMatchRootAndExactHaveEmptyParams(t *testing.T) {
	r := buildAPIRouter(t)
	mustMatch(t, r, "/", "root", nil)
	mustMatch(t, r, "/api/users/me", "me", map[string]string{})
}

func TestMatchOnEmptyRouter(t *testing.T) {
	r := New[string]()
	if _, _, ok := r.Match("/anything"); ok {
		t.Fatal("Match on an empty router matched")
	}
}

func TestWildcardOnlyPattern(t *testing.T) {
	r := New[string]()
	if err := r.Handle("/*any", "catch"); err != nil {
		t.Fatalf("Handle(/*any): %v", err)
	}
	mustMatch(t, r, "/zzz", "catch", map[string]string{"any": "zzz"})
	if _, _, ok := r.Match("/"); ok {
		t.Fatal("Match(/) matched /*any, but the wildcard needs a segment")
	}
}

func TestHandleRejectsInvalidWildcardPatterns(t *testing.T) {
	r := New[string]()
	if err := r.Handle("/a/*x/b", "h"); !errors.Is(err, ErrInvalidPattern) {
		t.Fatalf("mid-pattern wildcard: err = %v, want ErrInvalidPattern", err)
	}
	if err := r.Handle("/a/*", "h"); !errors.Is(err, ErrInvalidPattern) {
		t.Fatalf("unnamed wildcard: err = %v, want ErrInvalidPattern", err)
	}
	if err := r.Handle("/a/:", "h"); !errors.Is(err, ErrInvalidPattern) {
		t.Fatalf("unnamed param: err = %v, want ErrInvalidPattern", err)
	}
	if _, _, ok := r.Match("/a/anything"); ok {
		t.Fatal("a rejected pattern must not be routable")
	}
}

func TestLookupStaysLiteral(t *testing.T) {
	r := buildAPIRouter(t)
	// Lookup is the exact-string API and must not learn pattern matching.
	if _, ok := r.Lookup("/api/users/42"); ok {
		t.Fatal("Lookup(/api/users/42) matched; param matching belongs to Match only")
	}
	if h, ok := r.Lookup("/api/users/:id"); !ok || h != "user" {
		t.Fatalf("Lookup(/api/users/:id) = %q, %v; want the literally registered pattern", h, ok)
	}
}
