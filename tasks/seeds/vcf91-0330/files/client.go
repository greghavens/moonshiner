// Package vcfdiag diagnoses a failed deployment on the VCF Automation
// deployment API in VMware Cloud Foundation 9.1.
//
// A deployment that failed says very little about why. The request record
// carries a status and a short, usually unhelpful, details string. The reason
// lives further down: in the events the request emitted, and in the log lines
// attached to those events. This package walks that chain — requests, request,
// events, event logs — and reports the line that explains the failure.
package vcfdiag

import (
	"context"
	"errors"
	"fmt"
	"net/http"
)

// Config configures a Client.
type Config struct {
	// BaseURL is the scheme://host[:port] root of the VCF Automation API.
	BaseURL string

	// Token is the bearer token sent on every request.
	Token string

	// HTTPClient is optional. When nil the Client uses one of its own.
	HTTPClient *http.Client
}

// DiagnoseRequest selects the deployment to diagnose.
type DiagnoseRequest struct {
	// DeploymentID is the deployment whose most recent failure is diagnosed.
	// Required.
	DeploymentID string

	// PageSize, when greater than zero, is sent as the size query parameter on
	// the request and event listings. When zero the parameter is omitted.
	PageSize int

	// Search, when non-empty, is sent as the search query parameter on the
	// request listing. When empty the parameter is omitted.
	Search string
}

// LogLine is one line of an event's log, as retrieved.
type LogLine struct {
	// EventID is the event whose log this line came from.
	EventID string

	Rownum    int
	Message   string
	Timestamp string
}

// Diagnosis is the outcome of walking a failed deployment's request, events
// and event logs.
type Diagnosis struct {
	DeploymentID string

	// The failed request the diagnosis is about.
	RequestID      string
	RequestName    string
	RequestStatus  string
	RequestDetails string

	// The event whose log carried the root cause.
	EventID      string
	EventName    string
	ResourceName string

	// RootCause is the message of the log line that explains the failure.
	RootCause string

	// LogLines is every log line retrieved, in the order it was retrieved.
	LogLines []LogLine
}

// Errors reported by Diagnose.
var (
	// ErrInvalidRequest reports a malformed DiagnoseRequest, so no HTTP request
	// is made at all.
	ErrInvalidRequest = errors.New("vcfdiag: invalid request")

	// ErrNoFailedRequest reports that the deployment has no failed request to
	// diagnose.
	ErrNoFailedRequest = errors.New("vcfdiag: deployment has no failed request")

	// ErrNoRootCause reports that the logs were retrieved but none of them
	// carried an error line.
	ErrNoRootCause = errors.New("vcfdiag: no root cause found in event logs")
)

// APIError reports a non-2xx response from the VCF Automation API.
type APIError struct {
	// Op is the contract operation id that produced the response.
	Op string

	StatusCode int
	Body       string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("vcfdiag: %s: unexpected status %d: %s", e.Op, e.StatusCode, e.Body)
}

// Client talks to one VCF Automation endpoint.
type Client struct {
	baseURL string
	token   string
	hc      *http.Client
}

// New returns a Client for cfg.
func New(cfg Config) (*Client, error) {
	return nil, errors.New("vcfdiag: New not implemented")
}

// Diagnose walks the deployment's most recent failed request down to the log
// line that explains it.
func (c *Client) Diagnose(ctx context.Context, req DiagnoseRequest) (*Diagnosis, error) {
	return nil, errors.New("vcfdiag: Diagnose not implemented")
}
