package httpretry

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
)

// step scripts one response from the fake upstream.
type step struct {
	status int
	header map[string]string
	body   string
}

// scriptServer serves steps in order, repeating the last step once
// the script runs out. hits counts every request received.
func scriptServer(t *testing.T, steps ...step) (*httptest.Server, *atomic.Int32) {
	t.Helper()
	hits := new(atomic.Int32)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := int(hits.Add(1)) - 1
		if n >= len(steps) {
			n = len(steps) - 1
		}
		s := steps[n]
		for k, v := range s.header {
			w.Header().Set(k, v)
		}
		w.WriteHeader(s.status)
		io.WriteString(w, s.body)
	}))
	t.Cleanup(srv.Close)
	return srv, hits
}

func TestFirstTrySuccess(t *testing.T) {
	srv, hits := scriptServer(t, step{status: 200, body: "pong"})
	c := New(srv.Client(), 3)
	resp, err := c.Get(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 || string(body) != "pong" {
		t.Fatalf("got %d %q, want 200 pong", resp.StatusCode, body)
	}
	if hits.Load() != 1 {
		t.Fatalf("server saw %d requests, want 1", hits.Load())
	}
}

func TestRetriesServerErrorsUntilSuccess(t *testing.T) {
	srv, hits := scriptServer(t,
		step{status: 500}, step{status: 502}, step{status: 200, body: "ok"})
	c := New(srv.Client(), 3)
	resp, err := c.Get(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	if hits.Load() != 3 {
		t.Fatalf("server saw %d requests, want 3", hits.Load())
	}
}

func TestClientErrorsAreNotRetried(t *testing.T) {
	srv, hits := scriptServer(t, step{status: 404, body: "nope"})
	c := New(srv.Client(), 5)
	resp, err := c.Get(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 404 {
		t.Fatalf("status = %d, want 404", resp.StatusCode)
	}
	if hits.Load() != 1 {
		t.Fatalf("server saw %d requests, want exactly 1 (no retry on 4xx)", hits.Load())
	}
}

func TestExhaustedAttemptsReturnLastResponse(t *testing.T) {
	srv, hits := scriptServer(t, step{status: 503, body: "down"})
	c := New(srv.Client(), 3)
	resp, err := c.Get(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 503 {
		t.Fatalf("status = %d, want the final 503", resp.StatusCode)
	}
	if hits.Load() != 3 {
		t.Fatalf("server saw %d requests, want 3", hits.Load())
	}
}

func TestTransportErrorsSurfaceAfterRetries(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	url := srv.URL
	srv.Close() // nothing is listening anymore
	c := New(http.DefaultClient, 2)
	if _, err := c.Get(context.Background(), url); err == nil {
		t.Fatal("Get against a dead server must return an error")
	}
}

func TestCancelledContextStopsRetrying(t *testing.T) {
	srv, hits := scriptServer(t, step{status: 500})
	c := New(srv.Client(), 5)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := c.Get(ctx, srv.URL)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v, want context.Canceled", err)
	}
	if hits.Load() > 1 {
		t.Fatalf("server saw %d requests after cancellation, want at most 1", hits.Load())
	}
}

func TestNewClampsBadArguments(t *testing.T) {
	srv, hits := scriptServer(t, step{status: 200, body: "fine"})
	c := New(nil, 0)
	resp, err := c.Get(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 || hits.Load() != 1 {
		t.Fatalf("status %d after %d requests, want 200 after 1", resp.StatusCode, hits.Load())
	}
}
