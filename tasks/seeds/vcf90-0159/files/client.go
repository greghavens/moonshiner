package vcfautomation

import (
	"context"
	"errors"
	"net/http"
)

// Config contains the connection and OAuth values needed by VCF Automation.
type Config struct {
	BaseURL      string
	ClientID     string
	ClientSecret string
	AccessToken  string
	RefreshToken string
	HTTPClient   *http.Client
}

// CatalogItemRequest is the documented request body for a catalog deployment.
// Pointer fields distinguish an explicitly supplied zero from an unset value.
type CatalogItemRequest struct {
	BulkRequestCount *int           `json:"bulkRequestCount,omitempty"`
	DeploymentName   string         `json:"deploymentName,omitempty"`
	Inputs           map[string]any `json:"inputs,omitempty"`
	ProjectID        string         `json:"projectId,omitempty"`
	Reason           string         `json:"reason,omitempty"`
	Version          string         `json:"version,omitempty"`
}

// Deployment is the stable subset returned by Provision.
type Deployment struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	Status string `json:"status"`
}

// Client invokes the small VCF Automation contract shipped with this package.
type Client struct {
	config Config
}

// NewClient validates configuration without performing network IO.
func NewClient(config Config) (*Client, error) {
	if config.BaseURL == "" {
		return nil, errors.New("base URL is required")
	}
	if config.ClientID == "" || config.ClientSecret == "" {
		return nil, errors.New("OAuth client credentials are required")
	}
	if config.AccessToken == "" || config.RefreshToken == "" {
		return nil, errors.New("access and refresh tokens are required")
	}
	if config.HTTPClient == nil {
		config.HTTPClient = http.DefaultClient
	}
	return &Client{config: config}, nil
}

// Provision creates a deployment and retrieves it, refreshing authorization if
// the lookup observes an expired access token.
func (c *Client) Provision(context.Context, string, CatalogItemRequest) (Deployment, error) {
	return Deployment{}, errors.New("VCF Automation Provision is not implemented")
}
