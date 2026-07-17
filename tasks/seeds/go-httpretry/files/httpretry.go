// Package httpretry wraps an *http.Client with retry logic for GETs
// against our internal APIs. The service mesh restarts pods all day,
// so a transient 5xx or a torn connection should not bubble up to
// callers; a real client error (4xx) should, immediately.
package httpretry

import (
	"context"
	"io"
	"net/http"
)

// Client retries idempotent GET requests.
type Client struct {
	HTTP        *http.Client
	MaxAttempts int
}

// New returns a Client issuing at most maxAttempts tries per Get.
// A nil hc falls back to http.DefaultClient; maxAttempts below 1 is
// clamped to 1.
func New(hc *http.Client, maxAttempts int) *Client {
	if hc == nil {
		hc = http.DefaultClient
	}
	if maxAttempts < 1 {
		maxAttempts = 1
	}
	return &Client{HTTP: hc, MaxAttempts: maxAttempts}
}

// retryable reports whether a response status is worth another try.
func retryable(status int) bool {
	return status >= 500
}

// Get issues GET requests to url until one succeeds with a
// non-retryable status or MaxAttempts is exhausted. On exhaustion the
// last response (or last transport error) is returned so the caller
// can inspect it. Bodies of retried responses are drained and closed.
func (c *Client) Get(ctx context.Context, url string) (*http.Response, error) {
	attempts := c.MaxAttempts
	if attempts < 1 {
		attempts = 1
	}
	for i := 0; ; i++ {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		if err != nil {
			return nil, err
		}
		resp, derr := c.HTTP.Do(req)
		if derr == nil && !retryable(resp.StatusCode) {
			return resp, nil
		}
		if i == attempts-1 {
			return resp, derr
		}
		if derr == nil {
			io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
		}
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
	}
}
