package proxy

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
)

type rtFunc func(*http.Request) (*http.Response, error)

func (f rtFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

type timeoutErr struct{}

func (timeoutErr) Error() string   { return "read tcp 127.0.0.1:9000: i/o timeout" }
func (timeoutErr) Timeout() bool   { return true }
func (timeoutErr) Temporary() bool { return true }

func TestEndToEndThroughRealUpstream(t *testing.T) {
	type seen struct {
		path, query, host, xff, xfh, xfp, auth string
	}
	var got seen
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got = seen{
			path:  r.URL.Path,
			query: r.URL.RawQuery,
			host:  r.Host,
			xff:   r.Header.Get("X-Forwarded-For"),
			xfh:   r.Header.Get("X-Forwarded-Host"),
			xfp:   r.Header.Get("X-Forwarded-Proto"),
			auth:  r.Header.Get("Authorization"),
		}
		w.Header().Set("X-Backend", "hi")
		w.Header().Set("Keep-Alive", "timeout=5") // hop-by-hop: must not reach the client
		w.WriteHeader(http.StatusTeapot)
		io.WriteString(w, "teapot")
	}))
	t.Cleanup(upstream.Close)
	upURL, _ := url.Parse(upstream.URL)

	front := httptest.NewServer(New(upURL, "/edge"))
	t.Cleanup(front.Close)
	frontHost := strings.TrimPrefix(front.URL, "http://")

	req, _ := http.NewRequest(http.MethodGet, front.URL+"/edge/things/42?q=1", nil)
	req.Header.Set("Authorization", "Bearer tok")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()

	if resp.StatusCode != http.StatusTeapot || string(body) != "teapot" {
		t.Fatalf("client got %d %q, want the upstream's 418 \"teapot\"", resp.StatusCode, body)
	}
	if resp.Header.Get("X-Backend") != "hi" {
		t.Fatal("upstream response headers must pass through")
	}
	if v := resp.Header.Get("Keep-Alive"); v != "" {
		t.Fatalf("hop-by-hop response header Keep-Alive leaked to the client: %q", v)
	}

	if got.path != "/things/42" {
		t.Fatalf("upstream saw path %q, want /things/42 (prefix /edge stripped)", got.path)
	}
	if got.query != "q=1" {
		t.Fatalf("upstream saw query %q, want q=1", got.query)
	}
	if got.host != upURL.Host {
		t.Fatalf("upstream saw Host %q, want its own %q", got.host, upURL.Host)
	}
	if got.xff != "127.0.0.1" {
		t.Fatalf("upstream saw X-Forwarded-For %q, want 127.0.0.1", got.xff)
	}
	if got.xfh != frontHost {
		t.Fatalf("upstream saw X-Forwarded-Host %q, want the edge host %q", got.xfh, frontHost)
	}
	if got.xfp != "http" {
		t.Fatalf("upstream saw X-Forwarded-Proto %q, want http", got.xfp)
	}
	if got.auth != "Bearer tok" {
		t.Fatalf("Authorization did not pass through, upstream saw %q", got.auth)
	}
}

func TestEndToEndPostBodyForwarded(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		w.WriteHeader(http.StatusCreated)
		fmt.Fprintf(w, "echo:%s", b)
	}))
	t.Cleanup(upstream.Close)
	upURL, _ := url.Parse(upstream.URL)

	front := httptest.NewServer(New(upURL, "/edge"))
	t.Cleanup(front.Close)

	resp, err := http.Post(front.URL+"/edge/ingest", "application/json", strings.NewReader(`{"n":7}`))
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusCreated || string(body) != `echo:{"n":7}` {
		t.Fatalf("got %d %q, want 201 and the echoed body", resp.StatusCode, body)
	}
}

func TestUpstreamErrorMapping(t *testing.T) {
	upURL, _ := url.Parse("http://stats.internal:9000")
	cases := []struct {
		name       string
		err        error
		wantStatus int
	}{
		{"plain dial failure is 502", errors.New("dial tcp 10.0.0.9:9000: connect: connection refused"), http.StatusBadGateway},
		{"wrapped context deadline is 504", fmt.Errorf("round trip: %w", context.DeadlineExceeded), http.StatusGatewayTimeout},
		{"net timeout error is 504", timeoutErr{}, http.StatusGatewayTimeout},
		{"context canceled is plain 502", context.Canceled, http.StatusBadGateway},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			p := New(upURL, "")
			p.Transport = rtFunc(func(*http.Request) (*http.Response, error) { return nil, tc.err })
			rec := httptest.NewRecorder()
			req := httptest.NewRequest(http.MethodGet, "http://edge.example/x", nil)
			req.RemoteAddr = "203.0.113.7:1"
			p.ServeHTTP(rec, req)
			if rec.Code != tc.wantStatus {
				t.Fatalf("status = %d, want %d", rec.Code, tc.wantStatus)
			}
			if ct := rec.Header().Get("Content-Type"); !strings.HasPrefix(ct, "text/plain") {
				t.Fatalf("error Content-Type = %q, want text/plain", ct)
			}
			if rec.Body.Len() == 0 {
				t.Fatal("gateway errors need a body, monitoring greps for it")
			}
		})
	}
}

type closeTrackingBody struct {
	io.Reader
	closed atomic.Bool
}

func (b *closeTrackingBody) Close() error {
	b.closed.Store(true)
	return nil
}

func TestUpstreamResponseBodyClosedAndHopHeadersStripped(t *testing.T) {
	upURL, _ := url.Parse("http://stats.internal:9000")
	body := &closeTrackingBody{Reader: strings.NewReader(`{"ok":true}`)}

	p := New(upURL, "")
	p.Transport = rtFunc(func(*http.Request) (*http.Response, error) {
		h := http.Header{}
		h.Set("Content-Type", "application/json")
		h.Set("Connection", "x-internal-route")
		h.Set("X-Internal-Route", "rack42")
		h.Set("Keep-Alive", "timeout=5")
		h.Set("X-Cache", "HIT")
		h.Add("Set-Cookie", "a=1")
		h.Add("Set-Cookie", "b=2")
		return &http.Response{
			StatusCode: http.StatusOK,
			Header:     h,
			Body:       body,
		}, nil
	})

	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "http://edge.example/x", nil)
	req.RemoteAddr = "203.0.113.7:1"
	p.ServeHTTP(rec, req)

	if rec.Code != http.StatusOK || rec.Body.String() != `{"ok":true}` {
		t.Fatalf("got %d %q", rec.Code, rec.Body.String())
	}
	if !body.closed.Load() {
		t.Fatal("upstream response body was never closed — that leaks connections")
	}
	for _, gone := range []string{"Connection", "Keep-Alive", "X-Internal-Route"} {
		if v := rec.Header().Get(gone); v != "" {
			t.Fatalf("hop-by-hop response header %s leaked: %q", gone, v)
		}
	}
	if rec.Header().Get("X-Cache") != "HIT" {
		t.Fatal("end-to-end response headers must survive")
	}
	if got := rec.Header().Values("Set-Cookie"); len(got) != 2 || got[0] != "a=1" || got[1] != "b=2" {
		t.Fatalf("multi-valued response headers must copy completely, got %v", got)
	}
}
