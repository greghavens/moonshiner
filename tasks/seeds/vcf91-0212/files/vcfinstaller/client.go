// Package vcfinstaller implements the focused VCF Installer client.
package vcfinstaller

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"time"
)

// DeleteOptions controls one idempotent delete workflow. DepotType is a
// pointer so an omitted query option differs from an explicitly empty value.
type DeleteOptions struct {
	DepotType   *string
	MaxAttempts int
	RetryDelay  time.Duration
}

// APIError reports a non-success response from the contract operation.
type APIError struct {
	OperationID string
	StatusCode  int
	Attempts    int
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s failed with HTTP status %d on attempt %d", e.OperationID, e.StatusCode, e.Attempts)
}

// TransportError reports exhaustion after ambiguous transport failures.
type TransportError struct {
	OperationID string
	Attempts    int
}

func (e *TransportError) Error() string {
	return fmt.Sprintf("%s transport failed after %d attempts", e.OperationID, e.Attempts)
}

// Client calls the operation in the focused contract.
type Client struct {
	baseURL     string
	accessToken string
	httpClient  *http.Client
}

// ErrNotImplemented is returned by the exercise stub.
var ErrNotImplemented = errors.New("vcfinstaller client is not implemented")

// NewClient constructs a client for a VCF Installer service root.
func NewClient(baseURL, accessToken string, httpClient *http.Client) (*Client, error) {
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{baseURL: baseURL, accessToken: accessToken, httpClient: httpClient}, nil
}

// DeleteDepotSettings removes the selected depot settings with bounded,
// context-aware retries.
func (c *Client) DeleteDepotSettings(ctx context.Context, options DeleteOptions) error {
	return ErrNotImplemented
}
