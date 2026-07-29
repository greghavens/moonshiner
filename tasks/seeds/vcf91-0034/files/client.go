// Package runsnapshot retrieves a stable SDDC Manager run snapshot.
package runsnapshot

import (
	"context"
	"errors"
	"fmt"
	"net/http"
)

// ErrNotImplemented marks the incomplete integration.
var ErrNotImplemented = errors.New("run snapshot is not implemented")

// Config configures a Client.
type Config struct {
	BaseURL        string
	AccessToken    string
	RefreshTokenID string
	HTTPClient     *http.Client
}

// Domain is the focused getDomains element.
type Domain struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	Status string `json:"status,omitempty"`
	Type   string `json:"type,omitempty"`
}

// VCFError is the focused SDDC Manager Error shape.
type VCFError struct {
	ErrorCode          string `json:"errorCode,omitempty"`
	Message            string `json:"message,omitempty"`
	RemediationMessage string `json:"remediationMessage,omitempty"`
	ReferenceToken     string `json:"referenceToken,omitempty"`
}

// Task is the focused getTasks element.
type Task struct {
	ID                string     `json:"id"`
	Name              string     `json:"name"`
	Type              string     `json:"type,omitempty"`
	Status            string     `json:"status"`
	CreationTimestamp string     `json:"creationTimestamp"`
	Errors            []VCFError `json:"errors,omitempty"`
}

// Snapshot contains deterministically ordered collection results.
type Snapshot struct {
	Domains []Domain
	Tasks   []Task
}

// APIError preserves a structured non-success response.
type APIError struct {
	OperationID        string
	StatusCode         int
	ErrorCode          string
	Message            string
	RemediationMessage string
	ReferenceToken     string
}

func (e *APIError) Error() string {
	if e == nil {
		return "SDDC Manager API request failed"
	}
	return fmt.Sprintf(
		"SDDC Manager operation %s failed with HTTP %d",
		e.OperationID,
		e.StatusCode,
	)
}

// TransportError wraps transport failure while redacting its text.
type TransportError struct {
	OperationID string
	Err         error
}

func (e *TransportError) Error() string {
	if e == nil {
		return "SDDC Manager transport failed"
	}
	return fmt.Sprintf("SDDC Manager operation %s transport failed", e.OperationID)
}

func (e *TransportError) Unwrap() error {
	if e == nil {
		return nil
	}
	return e.Err
}

// ProtocolError reports malformed contract-success data.
type ProtocolError struct {
	OperationID string
	Reason      string
}

func (e *ProtocolError) Error() string {
	if e == nil {
		return "SDDC Manager protocol error"
	}
	return fmt.Sprintf(
		"SDDC Manager operation %s violated the response contract: %s",
		e.OperationID,
		e.Reason,
	)
}

// Client is a focused getDomains/getTasks client with token refresh.
type Client struct{}

// NewClient validates config without network traffic.
func NewClient(config Config) (*Client, error) {
	return nil, ErrNotImplemented
}

// Snapshot retrieves both collections, refreshing an expired access token
// without repeating a collection that already succeeded.
func (c *Client) Snapshot(ctx context.Context) (Snapshot, error) {
	return Snapshot{}, ErrNotImplemented
}
