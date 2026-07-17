// Package stockclient is a thin client for stockkeeper, the legacy
// warehouse inventory service we keep running until the platform migration
// finishes. stockkeeper speaks a small XML envelope format: requests are
// POSTed as <AdjustRequest>, and the reply is either <AdjustResponse> when
// the adjustment was applied or <ErrInfo> when the service processed the
// request and could not apply it (unknown location code, a delta that would
// take on-hand below zero, and so on).
//
// The client retries transient HTTP failures (connection errors and 5xx
// responses) but must not retry application errors — the service has
// already processed those, and sending the same record again only adds
// load and clutters the audit trail.
package stockclient

import (
	"bytes"
	"encoding/xml"
	"fmt"
	"io"
	"net/http"
)

// AdjustRequest is the XML body we POST for each stock adjustment.
type AdjustRequest struct {
	XMLName  xml.Name `xml:"AdjustRequest"`
	RecordID string   `xml:"RecordId"`
	Location string   `xml:"Location"`
	Delta    int      `xml:"Delta"`
}

// AdjustResponse is the reply when an adjustment was applied.
type AdjustResponse struct {
	XMLName  xml.Name `xml:"AdjustResponse"`
	RecordID string   `xml:"RecordId"`
	Status   string   `xml:"Status"`
	Ref      string   `xml:"Ref"`
}

// ErrInfo is stockkeeper's application-level error envelope.
type ErrInfo struct {
	XMLName xml.Name `xml:"ErrInfo"`
	Code    string   `xml:"Code"`
	Detail  string   `xml:"Detail"`
}

// PermanentError wraps an application error reported by stockkeeper.
// Sending the same request again will not change the outcome.
type PermanentError struct {
	Code   string
	Detail string
}

func (e *PermanentError) Error() string {
	return fmt.Sprintf("stockkeeper error %s: %s", e.Code, e.Detail)
}

// transientError marks an error as worth retrying.
type transientError struct{ cause error }

func (e *transientError) Error() string { return e.cause.Error() }

// Client sends stock adjustments to the stockkeeper endpoint.
type Client struct {
	endpoint   string
	httpClient *http.Client
	maxRetries int
}

// New creates a Client targeting the given endpoint URL.
// maxRetries is the number of retry attempts on transient failures (0 = no
// retries; the first attempt always happens).
func New(endpoint string, httpClient *http.Client, maxRetries int) *Client {
	return &Client{
		endpoint:   endpoint,
		httpClient: httpClient,
		maxRetries: maxRetries,
	}
}

// Apply sends one adjustment to stockkeeper. On an application error it
// returns *PermanentError immediately — no retries. On a transient error
// it retries up to maxRetries additional times.
func (c *Client) Apply(req AdjustRequest) (*AdjustResponse, error) {
	var lastErr error
	for attempt := 0; attempt <= c.maxRetries; attempt++ {
		resp, err := c.doOnce(req)
		if err == nil {
			return resp, nil
		}
		// Application errors are final: stop immediately.
		if _, ok := err.(*PermanentError); ok {
			return nil, err
		}
		lastErr = err
	}
	return nil, lastErr
}

// doOnce performs a single HTTP round-trip. It returns (*AdjustResponse, nil)
// on success, (*PermanentError, nil-response) on a parsed application error,
// or an error the caller treats as transient.
func (c *Client) doOnce(req AdjustRequest) (*AdjustResponse, error) {
	body, err := xml.Marshal(req)
	if err != nil {
		return nil, err
	}

	httpReq, err := http.NewRequest(http.MethodPost, c.endpoint, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	httpReq.Header.Set("Content-Type", "application/xml")

	httpResp, err := c.httpClient.Do(httpReq)
	if err != nil {
		// Network-level error — transient.
		return nil, &transientError{err}
	}
	defer httpResp.Body.Close()
	respBody, err := io.ReadAll(httpResp.Body)
	if err != nil {
		return nil, &transientError{err}
	}

	if httpResp.StatusCode == http.StatusOK {
		var out AdjustResponse
		if err := xml.Unmarshal(respBody, &out); err != nil {
			return nil, fmt.Errorf("stockclient: could not parse response: %w", err)
		}
		return &out, nil
	}

	if httpResp.StatusCode >= 500 {
		return nil, &transientError{
			fmt.Errorf("stockclient: server error %d", httpResp.StatusCode),
		}
	}

	return nil, fmt.Errorf("stockclient: unexpected status %d: %s", httpResp.StatusCode, respBody)
}
