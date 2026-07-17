// Tests for the shiplog deploy-event API.
//
// The EXISTING BEHAVIOR block passes against the shipped server.go and must
// keep passing. Everything below it specifies the per-client rate-limit
// middleware and fails until it exists.
package shiplog

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func doJSON(t *testing.T, method, url, body string) (*http.Response, map[string]any) {
	t.Helper()
	var rdr io.Reader
	if body != "" {
		rdr = strings.NewReader(body)
	}
	req, err := http.NewRequest(method, url, rdr)
	if err != nil {
		t.Fatal(err)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &m); err != nil {
			t.Fatalf("%s %s: body is not JSON: %v (%q)", method, url, err, raw)
		}
	}
	return resp, m
}

// ------------------------------------------------------------------ existing behavior

func TestCreateAndFetchEvents(t *testing.T) {
	srv := httptest.NewServer(NewServer())
	t.Cleanup(srv.Close)

	resp, m := doJSON(t, "POST", srv.URL+"/events", `{"service":"api","version":"v1.4.2","status":"started"}`)
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("create status = %d, want 201", resp.StatusCode)
	}
	if m["id"] != float64(1) || m["service"] != "api" || m["status"] != "started" {
		t.Fatalf("create body %v", m)
	}
	resp, m = doJSON(t, "POST", srv.URL+"/events", `{"service":"web","version":"v2.0.0","status":"succeeded"}`)
	if resp.StatusCode != http.StatusCreated || m["id"] != float64(2) {
		t.Fatalf("ids must be sequential, got %v", m)
	}

	resp, m = doJSON(t, "GET", srv.URL+"/events/1", "")
	if resp.StatusCode != http.StatusOK || m["service"] != "api" || m["version"] != "v1.4.2" {
		t.Fatalf("get event 1: %d %v", resp.StatusCode, m)
	}
}

func TestCreateValidation(t *testing.T) {
	srv := httptest.NewServer(NewServer())
	t.Cleanup(srv.Close)

	for name, body := range map[string]string{
		"malformed":       `{nope`,
		"missing service": `{"version":"v1","status":"started"}`,
		"missing version": `{"service":"api","status":"started"}`,
		"bad status":      `{"service":"api","version":"v1","status":"exploded"}`,
	} {
		resp, m := doJSON(t, "POST", srv.URL+"/events", body)
		if resp.StatusCode != http.StatusBadRequest {
			t.Fatalf("%s: status = %d, want 400", name, resp.StatusCode)
		}
		if msg, _ := m["error"].(string); msg == "" {
			t.Fatalf("%s: 400 body needs an error field, got %v", name, m)
		}
	}
}

func TestUnknownEventsAre404(t *testing.T) {
	srv := httptest.NewServer(NewServer())
	t.Cleanup(srv.Close)

	for _, path := range []string{"/events/99", "/events/notanumber"} {
		resp, m := doJSON(t, "GET", srv.URL+path, "")
		if resp.StatusCode != http.StatusNotFound {
			t.Fatalf("GET %s = %d, want 404", path, resp.StatusCode)
		}
		if msg, _ := m["error"].(string); msg == "" {
			t.Fatalf("404 body needs an error field, got %v", m)
		}
	}
}

func TestHealthz(t *testing.T) {
	srv := httptest.NewServer(NewServer())
	t.Cleanup(srv.Close)
	resp, m := doJSON(t, "GET", srv.URL+"/healthz", "")
	if resp.StatusCode != http.StatusOK || m["ok"] != true {
		t.Fatalf("healthz: %d %v", resp.StatusCode, m)
	}
}

// ------------------------------------------------------------------ rate limiting

type fakeClock struct {
	mu sync.Mutex
	t  time.Time
}

func newFakeClock() *fakeClock {
	return &fakeClock{t: time.Date(2026, 7, 1, 9, 0, 0, 0, time.UTC)}
}

func (c *fakeClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *fakeClock) Advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.t = c.t.Add(d)
}

// countingHandler stands in for the API so the tests can tell exactly how
// many requests made it through the limiter.
type countingHandler struct{ hits atomic.Int64 }

func (h *countingHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	h.hits.Add(1)
	w.Header().Set("X-Inner", "ran")
	w.WriteHeader(http.StatusTeapot)
	io.WriteString(w, "inner body")
}

func hit(h http.Handler, remoteAddr string, hdr map[string]string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodGet, "/events/1", nil)
	req.RemoteAddr = remoteAddr
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func TestBurstThenLimited(t *testing.T) {
	clock := newFakeClock()
	inner := &countingHandler{}
	h := RateLimit(inner, RateLimitConfig{Rate: 1, Burst: 3, Now: clock.Now})

	for i := 0; i < 3; i++ {
		rec := hit(h, "10.0.0.1:1111", nil)
		if rec.Code != http.StatusTeapot {
			t.Fatalf("request %d within the burst: status = %d, want the inner 418", i+1, rec.Code)
		}
		if rec.Header().Get("X-Inner") != "ran" || rec.Body.String() != "inner body" {
			t.Fatalf("request %d: limiter must pass the inner response through untouched", i+1)
		}
	}

	rec := hit(h, "10.0.0.1:1111", nil)
	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("request over the burst: status = %d, want 429", rec.Code)
	}
	if ct := rec.Header().Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		t.Fatalf("429 Content-Type = %q, want application/json", ct)
	}
	var m map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &m); err != nil || m["error"] != "rate limit exceeded" {
		t.Fatalf(`429 body = %q, want {"error":"rate limit exceeded"}`, rec.Body.String())
	}
	if got := inner.hits.Load(); got != 3 {
		t.Fatalf("inner handler saw %d requests, want 3 — 429s must not reach it", got)
	}
}

func TestRetryAfterCountsDownWithTheClock(t *testing.T) {
	clock := newFakeClock()
	inner := &countingHandler{}
	// One token every 2 seconds, two on hand at the start.
	h := RateLimit(inner, RateLimitConfig{Rate: 0.5, Burst: 2, Now: clock.Now})

	addr := "10.0.0.2:999"
	for i := 0; i < 2; i++ {
		if rec := hit(h, addr, nil); rec.Code != http.StatusTeapot {
			t.Fatalf("burst request %d: %d", i+1, rec.Code)
		}
	}
	rec := hit(h, addr, nil)
	if rec.Code != http.StatusTooManyRequests || rec.Header().Get("Retry-After") != "2" {
		t.Fatalf("empty bucket: status %d Retry-After %q, want 429 with \"2\"", rec.Code, rec.Header().Get("Retry-After"))
	}

	clock.Advance(time.Second)
	rec = hit(h, addr, nil)
	if rec.Code != http.StatusTooManyRequests || rec.Header().Get("Retry-After") != "1" {
		t.Fatalf("1s later: status %d Retry-After %q, want 429 with \"1\"", rec.Code, rec.Header().Get("Retry-After"))
	}

	clock.Advance(time.Second)
	if rec := hit(h, addr, nil); rec.Code != http.StatusTeapot {
		t.Fatalf("after a full refill interval the request must pass, got %d", rec.Code)
	}
	rec = hit(h, addr, nil)
	if rec.Code != http.StatusTooManyRequests || rec.Header().Get("Retry-After") != "2" {
		t.Fatalf("bucket drained again: status %d Retry-After %q, want 429 with \"2\"", rec.Code, rec.Header().Get("Retry-After"))
	}
}

func TestRetryAfterRoundsUp(t *testing.T) {
	clock := newFakeClock()
	h := RateLimit(&countingHandler{}, RateLimitConfig{Rate: 1, Burst: 1, Now: clock.Now})

	if rec := hit(h, "10.0.0.3:1", nil); rec.Code != http.StatusTeapot {
		t.Fatalf("first request: %d", rec.Code)
	}
	clock.Advance(300 * time.Millisecond)
	rec := hit(h, "10.0.0.3:1", nil)
	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("0.3 tokens is not a token: %d", rec.Code)
	}
	if got := rec.Header().Get("Retry-After"); got != "1" {
		t.Fatalf("Retry-After = %q, want \"1\" (0.7s rounded UP, never 0)", got)
	}
}

func TestRefillNeverExceedsBurst(t *testing.T) {
	clock := newFakeClock()
	inner := &countingHandler{}
	h := RateLimit(inner, RateLimitConfig{Rate: 100, Burst: 2, Now: clock.Now})

	addr := "10.0.0.4:1"
	hit(h, addr, nil)
	clock.Advance(1000 * time.Second) // ages pass; the bucket still caps at 2
	ok := 0
	for i := 0; i < 5; i++ {
		if rec := hit(h, addr, nil); rec.Code == http.StatusTeapot {
			ok++
		}
	}
	if ok != 2 {
		t.Fatalf("after a long idle stretch %d requests passed, want exactly Burst=2", ok)
	}
}

func TestClientsAreIsolatedByIPWithoutPort(t *testing.T) {
	clock := newFakeClock()
	h := RateLimit(&countingHandler{}, RateLimitConfig{Rate: 1, Burst: 1, Now: clock.Now})

	if rec := hit(h, "10.0.0.5:1111", nil); rec.Code != http.StatusTeapot {
		t.Fatalf("first client first request: %d", rec.Code)
	}
	// same IP, different source port: same bucket
	if rec := hit(h, "10.0.0.5:2222", nil); rec.Code != http.StatusTooManyRequests {
		t.Fatalf("same IP from another port must share the bucket, got %d", rec.Code)
	}
	// different IP: fresh bucket
	if rec := hit(h, "10.0.0.6:1111", nil); rec.Code != http.StatusTeapot {
		t.Fatalf("a different client must not be affected, got %d", rec.Code)
	}
}

func TestCustomKeyFunc(t *testing.T) {
	clock := newFakeClock()
	byAPIKey := func(r *http.Request) string { return r.Header.Get("X-Api-Key") }
	h := RateLimit(&countingHandler{}, RateLimitConfig{Rate: 1, Burst: 1, Now: clock.Now, KeyFunc: byAPIKey})

	if rec := hit(h, "10.0.0.7:1", map[string]string{"X-Api-Key": "alpha"}); rec.Code != http.StatusTeapot {
		t.Fatalf("alpha #1: %d", rec.Code)
	}
	// same key from a different address still shares the bucket
	if rec := hit(h, "10.99.99.99:1", map[string]string{"X-Api-Key": "alpha"}); rec.Code != http.StatusTooManyRequests {
		t.Fatalf("alpha from elsewhere: %d, want 429", rec.Code)
	}
	if rec := hit(h, "10.0.0.7:1", map[string]string{"X-Api-Key": "beta"}); rec.Code != http.StatusTeapot {
		t.Fatalf("beta must have its own bucket: %d", rec.Code)
	}
}

func TestBypassListIsNeverLimited(t *testing.T) {
	clock := newFakeClock()
	inner := &countingHandler{}
	h := RateLimit(inner, RateLimitConfig{Rate: 1, Burst: 1, Now: clock.Now, Bypass: []string{"10.8.8.8"}})

	for i := 0; i < 10; i++ {
		if rec := hit(h, "10.8.8.8:70", nil); rec.Code != http.StatusTeapot {
			t.Fatalf("bypassed client got %d on request %d", rec.Code, i+1)
		}
	}
	// a normal client is still limited
	hit(h, "10.0.0.9:1", nil)
	if rec := hit(h, "10.0.0.9:1", nil); rec.Code != http.StatusTooManyRequests {
		t.Fatalf("non-bypassed client: %d, want 429", rec.Code)
	}
}

func TestNilNowDefaultsToWallClock(t *testing.T) {
	// No assertions about timing here — just that a nil Now works and a
	// burst-sized volley passes.
	h := RateLimit(&countingHandler{}, RateLimitConfig{Rate: 1, Burst: 5})
	for i := 0; i < 5; i++ {
		if rec := hit(h, "10.0.0.10:1", nil); rec.Code != http.StatusTeapot {
			t.Fatalf("request %d with default clock: %d", i+1, rec.Code)
		}
	}
}

func TestConcurrentRequestsConsumeExactlyBurst(t *testing.T) {
	clock := newFakeClock()
	inner := &countingHandler{}
	h := RateLimit(inner, RateLimitConfig{Rate: 1, Burst: 5, Now: clock.Now})

	const n = 25
	var ok, limited atomic.Int64
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			rec := hit(h, "10.0.0.11:1", nil)
			switch rec.Code {
			case http.StatusTeapot:
				ok.Add(1)
			case http.StatusTooManyRequests:
				limited.Add(1)
			}
		}()
	}
	wg.Wait()
	if ok.Load() != 5 || limited.Load() != n-5 {
		t.Fatalf("under concurrency: %d passed / %d limited, want exactly 5 / %d", ok.Load(), limited.Load(), n-5)
	}
	if got := inner.hits.Load(); got != 5 {
		t.Fatalf("inner handler saw %d requests, want exactly 5", got)
	}
}

func TestRateLimitedAPIEndToEnd(t *testing.T) {
	clock := newFakeClock()
	byAPIKey := func(r *http.Request) string { return r.Header.Get("X-Api-Key") }
	srv := httptest.NewServer(RateLimit(NewServer(), RateLimitConfig{
		Rate: 0.5, Burst: 2, Now: clock.Now, KeyFunc: byAPIKey, Bypass: []string{"internal-cron"},
	}))
	t.Cleanup(srv.Close)

	call := func(method, path, body, key string) (*http.Response, map[string]any) {
		t.Helper()
		var rdr io.Reader
		if body != "" {
			rdr = strings.NewReader(body)
		}
		req, err := http.NewRequest(method, srv.URL+path, rdr)
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("X-Api-Key", key)
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		raw, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		var m map[string]any
		if len(raw) > 0 {
			json.Unmarshal(raw, &m)
		}
		return resp, m
	}

	resp, m := call("POST", "/events", `{"service":"api","version":"v9","status":"started"}`, "ci-bot")
	if resp.StatusCode != http.StatusCreated || m["id"] != float64(1) {
		t.Fatalf("create through the limiter: %d %v", resp.StatusCode, m)
	}
	resp, m = call("GET", "/events/1", "", "ci-bot")
	if resp.StatusCode != http.StatusOK || m["service"] != "api" {
		t.Fatalf("read through the limiter: %d %v", resp.StatusCode, m)
	}

	resp, m = call("GET", "/events/1", "", "ci-bot")
	if resp.StatusCode != http.StatusTooManyRequests {
		t.Fatalf("third call in the window: %d, want 429", resp.StatusCode)
	}
	if resp.Header.Get("Retry-After") != "2" {
		t.Fatalf("Retry-After = %q, want \"2\"", resp.Header.Get("Retry-After"))
	}
	if m["error"] != "rate limit exceeded" {
		t.Fatalf("429 body %v", m)
	}

	// the bypassed key sails through regardless
	for i := 0; i < 6; i++ {
		if resp, _ := call("GET", "/healthz", "", "internal-cron"); resp.StatusCode != http.StatusOK {
			t.Fatalf("bypassed key blocked on call %d: %d", i+1, resp.StatusCode)
		}
	}

	clock.Advance(2 * time.Second)
	if resp, _ := call("GET", "/events/1", "", "ci-bot"); resp.StatusCode != http.StatusOK {
		t.Fatalf("after the clock advances a token must be back: %d", resp.StatusCode)
	}
}
