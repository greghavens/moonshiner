package httpretry

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"reflect"
	"sync/atomic"
	"testing"
	"time"
)

// Acceptance tests for retry pacing: exponential backoff between
// attempts, Retry-After honoring (with 429 now retryable), a
// per-attempt context timeout, and the injectable Sleep hook that
// keeps all of this testable without wall-clock waits.

// sleepRecorder captures every delay the client asks for.
type sleepRecorder struct {
	delays []time.Duration
	err    error // returned from every call when non-nil
}

func (r *sleepRecorder) sleep(ctx context.Context, d time.Duration) error {
	r.delays = append(r.delays, d)
	return r.err
}

func drainAndClose(t *testing.T, resp *http.Response) {
	t.Helper()
	if resp != nil {
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
	}
}

func TestBackoffDoublesFromBaseAndIsCapped(t *testing.T) {
	srv, hits := scriptServer(t, step{status: 500})
	rec := &sleepRecorder{}
	c := &Client{
		HTTP:        srv.Client(),
		MaxAttempts: 5,
		BaseDelay:   100 * time.Millisecond,
		MaxDelay:    400 * time.Millisecond,
		Sleep:       rec.sleep,
	}
	resp, err := c.Get(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	drainAndClose(t, resp)
	if hits.Load() != 5 {
		t.Fatalf("server saw %d requests, want 5", hits.Load())
	}
	want := []time.Duration{
		100 * time.Millisecond,
		200 * time.Millisecond,
		400 * time.Millisecond,
		400 * time.Millisecond, // capped, and none after the final attempt
	}
	if !reflect.DeepEqual(rec.delays, want) {
		t.Fatalf("sleeps = %v, want %v", rec.delays, want)
	}
}

func TestZeroBaseDelayMeansNoSleeps(t *testing.T) {
	srv, hits := scriptServer(t, step{status: 500}, step{status: 500}, step{status: 200, body: "ok"})
	rec := &sleepRecorder{}
	c := &Client{HTTP: srv.Client(), MaxAttempts: 4, Sleep: rec.sleep}
	resp, err := c.Get(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if string(body) != "ok" || hits.Load() != 3 {
		t.Fatalf("got %q after %d requests, want ok after 3", body, hits.Load())
	}
	if len(rec.delays) != 0 {
		t.Fatalf("Sleep called with %v, want no calls when BaseDelay is 0", rec.delays)
	}
}

func TestRetryAfterSecondsOverridesComputedBackoff(t *testing.T) {
	srv, hits := scriptServer(t,
		step{status: 429, header: map[string]string{"Retry-After": "2"}},
		step{status: 200, body: "ok"})
	rec := &sleepRecorder{}
	c := &Client{
		HTTP:        srv.Client(),
		MaxAttempts: 3,
		BaseDelay:   100 * time.Millisecond,
		Sleep:       rec.sleep,
	}
	resp, err := c.Get(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	drainAndClose(t, resp)
	if resp.StatusCode != 200 || hits.Load() != 2 {
		t.Fatalf("status %d after %d requests, want 200 after 2", resp.StatusCode, hits.Load())
	}
	if want := []time.Duration{2 * time.Second}; !reflect.DeepEqual(rec.delays, want) {
		t.Fatalf("sleeps = %v, want %v (server-provided delay wins)", rec.delays, want)
	}
}

func TestRetryAfterIsStillCappedByMaxDelay(t *testing.T) {
	srv, _ := scriptServer(t,
		step{status: 503, header: map[string]string{"Retry-After": "10"}},
		step{status: 200})
	rec := &sleepRecorder{}
	c := &Client{
		HTTP:        srv.Client(),
		MaxAttempts: 2,
		BaseDelay:   50 * time.Millisecond,
		MaxDelay:    time.Second,
		Sleep:       rec.sleep,
	}
	resp, err := c.Get(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	drainAndClose(t, resp)
	if want := []time.Duration{time.Second}; !reflect.DeepEqual(rec.delays, want) {
		t.Fatalf("sleeps = %v, want %v", rec.delays, want)
	}
}

func TestUnparseableRetryAfterFallsBackToBackoff(t *testing.T) {
	srv, _ := scriptServer(t,
		step{status: 429, header: map[string]string{"Retry-After": "soonish"}},
		step{status: 200})
	rec := &sleepRecorder{}
	c := &Client{
		HTTP:        srv.Client(),
		MaxAttempts: 2,
		BaseDelay:   100 * time.Millisecond,
		Sleep:       rec.sleep,
	}
	resp, err := c.Get(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	drainAndClose(t, resp)
	if want := []time.Duration{100 * time.Millisecond}; !reflect.DeepEqual(rec.delays, want) {
		t.Fatalf("sleeps = %v, want %v", rec.delays, want)
	}
}

func TestRetryAfterHonoredEvenWithZeroBaseDelay(t *testing.T) {
	srv, _ := scriptServer(t,
		step{status: 429, header: map[string]string{"Retry-After": "1"}},
		step{status: 200})
	rec := &sleepRecorder{}
	c := &Client{HTTP: srv.Client(), MaxAttempts: 2, Sleep: rec.sleep}
	resp, err := c.Get(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	drainAndClose(t, resp)
	if want := []time.Duration{time.Second}; !reflect.DeepEqual(rec.delays, want) {
		t.Fatalf("sleeps = %v, want %v", rec.delays, want)
	}
}

func TestTooManyRequestsWithoutHeaderUsesBackoff(t *testing.T) {
	srv, hits := scriptServer(t, step{status: 429}, step{status: 200})
	rec := &sleepRecorder{}
	c := &Client{
		HTTP:        srv.Client(),
		MaxAttempts: 2,
		BaseDelay:   100 * time.Millisecond,
		Sleep:       rec.sleep,
	}
	resp, err := c.Get(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	drainAndClose(t, resp)
	if resp.StatusCode != 200 || hits.Load() != 2 {
		t.Fatalf("status %d after %d requests, want 429 retried into a 200", resp.StatusCode, hits.Load())
	}
	if want := []time.Duration{100 * time.Millisecond}; !reflect.DeepEqual(rec.delays, want) {
		t.Fatalf("sleeps = %v, want %v", rec.delays, want)
	}
}

func TestPerAttemptTimeoutCutsOffStuckAttempts(t *testing.T) {
	hits := new(atomic.Int32)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		<-r.Context().Done() // never answer; hold the request open
	}))
	t.Cleanup(srv.Close)

	rec := &sleepRecorder{}
	c := &Client{
		HTTP:              srv.Client(),
		MaxAttempts:       2,
		PerAttemptTimeout: 80 * time.Millisecond,
		Sleep:             rec.sleep,
	}
	ctx := context.Background()
	_, err := c.Get(ctx, srv.URL)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("err = %v, want a deadline-exceeded error", err)
	}
	if hits.Load() != 2 {
		t.Fatalf("server saw %d requests, want 2 (each attempt gets its own timeout)", hits.Load())
	}
	if ctx.Err() != nil {
		t.Fatalf("parent context reports %v, want untouched", ctx.Err())
	}
}

func TestSleepErrorAbortsRemainingAttempts(t *testing.T) {
	srv, hits := scriptServer(t, step{status: 500})
	rec := &sleepRecorder{err: context.Canceled}
	c := &Client{
		HTTP:        srv.Client(),
		MaxAttempts: 5,
		BaseDelay:   time.Minute, // would be painful if actually slept
		Sleep:       rec.sleep,
	}
	_, err := c.Get(context.Background(), srv.URL)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v, want context.Canceled from the aborted sleep", err)
	}
	if hits.Load() != 1 {
		t.Fatalf("server saw %d requests, want 1 (no attempt after the aborted sleep)", hits.Load())
	}
	if len(rec.delays) != 1 {
		t.Fatalf("Sleep called %d times, want once", len(rec.delays))
	}
}

func TestNilSleepFallsBackToRealTimer(t *testing.T) {
	srv, hits := scriptServer(t, step{status: 500}, step{status: 200, body: "ok"})
	c := &Client{
		HTTP:        srv.Client(),
		MaxAttempts: 2,
		BaseDelay:   time.Millisecond,
	}
	resp, err := c.Get(context.Background(), srv.URL)
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if string(body) != "ok" || hits.Load() != 2 {
		t.Fatalf("got %q after %d requests, want ok after 2", body, hits.Load())
	}
}
