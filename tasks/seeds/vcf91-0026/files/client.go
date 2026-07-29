// Package domainsnapshot retrieves a complete SDDC Manager domain inventory.
package domainsnapshot

import (
	"context"
	"errors"
	"net/http"
)

// Config configures an SDDC Manager client.
type Config struct {
	BaseURL    string
	Username   string
	Password   string
	HTTPClient *http.Client
	PageSize   int
}

// TokenCreationSpec is the createToken request model.
type TokenCreationSpec struct {
	Username string  `json:"username"`
	Password string  `json:"password"`
	APIKey   *string `json:"apiKey,omitempty"`
	IDToken  *string `json:"idToken,omitempty"`
}

// Domain preserves an intact domain response object.
type Domain map[string]any

// APIError represents a non-success response from an operation.
type APIError struct {
	OperationID string
	StatusCode  int
	ErrorCode   string
	Message     string
}

func (e *APIError) Error() string { return "SDDC Manager API request failed" }

// ProtocolError represents a malformed or inconsistent success response.
type ProtocolError struct {
	OperationID string
	Reason      string
}

func (e *ProtocolError) Error() string { return "SDDC Manager response violated the contract" }

// Client is an SDDC Manager domain snapshot client.
type Client struct{}

// NewClient validates config without performing network I/O.
func NewClient(config Config) (*Client, error) {
	return nil, errors.New("not implemented")
}

// ListDomains returns the complete domain collection.
func (c *Client) ListDomains(ctx context.Context) ([]Domain, error) {
	return nil, errors.New("not implemented")
}
