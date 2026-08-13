// Package vcfinstaller implements the focused VCF Installer client.
package vcfinstaller

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"sync"
)

// Task is the result projection returned by ListTasks. Type is nil when the
// optional response member was absent.
type Task struct {
	ID                string  `json:"id"`
	Name              string  `json:"name"`
	Type              *string `json:"type,omitempty"`
	Status            string  `json:"status"`
	CreationTimestamp string  `json:"creationTimestamp"`
}

// APIError reports a non-success response from a focused contract operation.
type APIError struct {
	OperationID string
	StatusCode  int
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s failed with HTTP status %d", e.OperationID, e.StatusCode)
}

// ProtocolError reports a malformed or inconsistent successful response.
type ProtocolError struct {
	OperationID string
	Problem     string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("%s protocol error: %s", e.OperationID, e.Problem)
}

// Client calls the two operations in the focused VCF Installer contract.
type Client struct {
	baseURL        string
	accessToken    string
	refreshTokenID string
	httpClient     *http.Client
	workflowMu     sync.Mutex
}

// ErrNotImplemented is returned by the exercise stub.
var ErrNotImplemented = errors.New("VCF Installer task workflow is not implemented")

// NewClient constructs a focused VCF Installer client. A nil HTTP client uses
// http.DefaultClient.
func NewClient(baseURL, accessToken, refreshTokenID string, httpClient *http.Client) (*Client, error) {
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{
		baseURL:        baseURL,
		accessToken:    accessToken,
		refreshTokenID: refreshTokenID,
		httpClient:     httpClient,
	}, nil
}

// ListTasks retrieves, validates, and stably sorts the complete task
// collection. It refreshes the access token once when getTasks first returns
// HTTP 401 and resumes at the interrupted page.
func (c *Client) ListTasks(ctx context.Context, pageSize int) ([]Task, error) {
	return nil, ErrNotImplemented
}
