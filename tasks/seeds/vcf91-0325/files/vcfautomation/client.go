package vcfautomation

import (
	"context"
	"errors"
	"net/http"
)

// Deployment is the focused Get Deployments representation used by this
// package. Pointer fields are optional in the response.
type Deployment struct {
	ID            string  `json:"id"`
	Name          string  `json:"name"`
	Status        string  `json:"status"`
	OrgID         string  `json:"orgId"`
	ProjectID     string  `json:"projectId"`
	CreatedAt     string  `json:"createdAt"`
	Description   *string `json:"description,omitempty"`
	LastUpdatedAt *string `json:"lastUpdatedAt,omitempty"`
	OwnedBy       *string `json:"ownedBy,omitempty"`
}

// ListDeploymentsOptions carries the Get Deployments filters this package
// supports. Every field except PageSize is optional; a nil pointer or an empty
// slice means the corresponding query parameter is not set.
type ListDeploymentsOptions struct {
	// PageSize is sent as the size query parameter on every request.
	PageSize int

	Sort          *string
	Search        *string
	Name          *string
	Status        []string
	Projects      []string
	ResourceTypes []string
	OwnedBy       []string
	Deleted       *bool
}

// APIError reports a non-200 response. The reference documentation records no
// error body schema for Get Deployments, so only the status code and the raw
// body are carried.
type APIError struct {
	StatusCode int
	Body       string
}

func (e *APIError) Error() string { return "VCF Automation API request failed" }

// ProtocolError reports a 200 response that is malformed or internally
// inconsistent with the page that was requested.
type ProtocolError struct{ Reason string }

func (e *ProtocolError) Error() string { return "VCF Automation protocol error: " + e.Reason }

// Client calls the VCF Automation API.
type Client struct{}

// NewClient constructs a client. It is intentionally incomplete.
func NewClient(baseURL, accessToken string, httpClient *http.Client) (*Client, error) {
	return nil, errors.New("TODO: implement NewClient")
}

// ListAllDeployments retrieves the complete deployment collection and returns
// it in a deterministic order. It is intentionally incomplete.
func (c *Client) ListAllDeployments(ctx context.Context, options ListDeploymentsOptions) ([]Deployment, error) {
	return nil, errors.New("TODO: implement ListAllDeployments")
}
