// Package depotdelete implements the task-scoped SDDC Manager integration.
package depotdelete

import (
	"context"
	"errors"
	"net/http"
)

// ErrNotImplemented is returned by the initial scaffold.
var ErrNotImplemented = errors.New("SDDC Manager depot deletion is not implemented")

// Config configures a depot deletion client.
type Config struct {
	BaseURL     string
	AccessToken string
	HTTPClient  *http.Client
	MaxAttempts int
	BeforeRetry func(context.Context, int) error
}

// DeleteDepotOptions contains the optional deleteDepotSettings query field.
type DeleteDepotOptions struct {
	DepotType *string
}

// Result describes how many DELETE submissions were needed.
type Result struct {
	Attempts int
	Retried  bool
}

// APIError represents an HTTP response that is not the documented success.
type APIError struct {
	OperationID        string
	StatusCode         int
	ErrorCode          string
	Message            string
	RemediationMessage string
	ReferenceToken     string
}

func (e *APIError) Error() string {
	return "SDDC Manager API request failed"
}

// TransportError represents a terminal request transport failure.
type TransportError struct {
	OperationID string
}

func (e *TransportError) Error() string {
	return "SDDC Manager transport request failed"
}

// Client deletes SDDC Manager depot settings.
type Client struct{}

// NewClient validates config without performing network I/O.
func NewClient(config Config) (*Client, error) {
	return nil, ErrNotImplemented
}

// DeleteDepotSettings safely retries the idempotent deleteDepotSettings call.
func (c *Client) DeleteDepotSettings(
	ctx context.Context,
	options DeleteDepotOptions,
) (Result, error) {
	return Result{}, ErrNotImplemented
}
