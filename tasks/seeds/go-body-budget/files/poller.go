// Package poller tracks long-running ingest jobs by polling their
// status endpoint with one shared HTTP client.
package poller

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
)

// Status is the decoded body of a healthy status response.
type Status struct {
	State    string `json:"state"`
	Progress int    `json:"progress"`
}

// StatusError carries a terminal HTTP status plus the service's
// structured message; operators grep dashboards for Message verbatim.
type StatusError struct {
	Code    int
	Message string
}

func (e *StatusError) Error() string {
	return fmt.Sprintf("status %d: %s", e.Code, e.Message)
}

// Client polls one status URL. MaxAttempts bounds how many responses
// it will consume per Poll, including 429/503 retries.
type Client struct {
	HTTPClient  *http.Client
	MaxAttempts int
}

// Poll fetches url until the job reports a decodable 200 body, a
// terminal status arrives, or the retry budget runs out. Every
// response body must be read to EOF and closed on every path so the
// shared transport can return its connection to the pool; a long
// polling loop must never starve the transport.
func (c *Client) Poll(ctx context.Context, url string) (*Status, error) {
	lastCode := 0
	for attempt := 0; attempt < c.MaxAttempts; attempt++ {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		if err != nil {
			return nil, err
		}
		resp, err := c.HTTPClient.Do(req)
		if err != nil {
			return nil, fmt.Errorf("poll %s: %w", url, err)
		}
		if resp.StatusCode == http.StatusTooManyRequests || resp.StatusCode == http.StatusServiceUnavailable {
			lastCode = resp.StatusCode
			continue
		}
		if resp.StatusCode != http.StatusOK {
			var payload struct {
				Error string `json:"error"`
			}
			_ = json.NewDecoder(resp.Body).Decode(&payload)
			return nil, &StatusError{Code: resp.StatusCode, Message: payload.Error}
		}
		var st Status
		if err := json.NewDecoder(resp.Body).Decode(&st); err != nil {
			return nil, fmt.Errorf("decode status: %w", err)
		}
		resp.Body.Close()
		return &st, nil
	}
	return nil, &StatusError{Code: lastCode, Message: "retry budget exhausted"}
}
