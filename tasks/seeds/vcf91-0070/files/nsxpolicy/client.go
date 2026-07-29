// Package nsxpolicy contains the focused VCF 9.1 NSX Policy client
// described in the task.
package nsxpolicy

import (
	"context"
	"net/http"
)

// RefreshFunc exchanges an access token rejected by the server for a new one.
type RefreshFunc func(context.Context, string) (string, error)

// Config configures a Client without performing an API request.
type Config struct {
	BaseURL     string
	AccessToken string
	HTTPClient  *http.Client
	Refresh     RefreshFunc
}

// Group is the response projection used by ListGroupForDomain.
type Group struct {
	ID           string `json:"id"`
	DisplayName  string `json:"display_name"`
	Path         string `json:"path"`
	ResourceType string `json:"resource_type"`
}

// Client invokes the focused NSX Policy contract.
type Client struct{}

// APIError represents a non-successful HTTP response.
type APIError struct {
	StatusCode   int
	ErrorCode    int64
	ErrorMessage string
	ModuleName   string
	Details      string
}

func (e *APIError) Error() string {
	return "NSX Policy request failed"
}

// ProtocolError reports a successful response that violates the contract.
type ProtocolError struct {
	Message string
}

func (e *ProtocolError) Error() string {
	return e.Message
}

// NewClient validates cfg and returns an independent client.
func NewClient(cfg Config) (*Client, error) {
	return nil, &ProtocolError{Message: "TODO: validate configuration and create the client"}
}

// ListAllGroups lists every page for one domain.
func (c *Client) ListAllGroups(ctx context.Context, domainID string) ([]Group, error) {
	return nil, &ProtocolError{Message: "TODO: implement ListGroupForDomain pagination and token refresh"}
}
