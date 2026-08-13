// Package vcfinstaller implements the focused VCF Installer client.
package vcfinstaller

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"time"
)

// ProxyConfiguration is the writable projection of the specification model.
// Pointer-valued fields distinguish an omitted option from an explicit zero value.
type ProxyConfiguration struct {
	IsEnabled        bool    `json:"isEnabled"`
	Host             *string `json:"host,omitempty"`
	Port             *int32  `json:"port,omitempty"`
	TransferProtocol *string `json:"transferProtocol,omitempty"`
	Username         *string `json:"username,omitempty"`
	Password         *string `json:"password,omitempty"`
	IsAuthenticated  *bool   `json:"isAuthenticated,omitempty"`
}

// Task contains the required fields used by the polling workflow.
type Task struct {
	ID                string `json:"id"`
	Name              string `json:"name"`
	Status            string `json:"status"`
	CreationTimestamp string `json:"creationTimestamp"`
}

// APIError reports a non-success response for one of the contract operations.
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

// TaskFailedError reports a recognized non-success terminal task state.
type TaskFailedError struct {
	Task Task
}

func (e *TaskFailedError) Error() string {
	return fmt.Sprintf("VCF Installer task %q ended with status %q", e.Task.ID, e.Task.Status)
}

// Client calls the two operations in the focused contract.
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

// UpdateProxyAndWait submits the update and polls its task to a terminal state.
func (c *Client) UpdateProxyAndWait(
	ctx context.Context,
	config ProxyConfiguration,
	pollInterval time.Duration,
) (Task, error) {
	return Task{}, ErrNotImplemented
}
