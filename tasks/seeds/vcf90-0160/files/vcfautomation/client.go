// Package vcfautomation implements the small VCF Automation surface described
// by docs/contract.json.
package vcfautomation

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
)

// ErrNotImplemented marks the exercise's unfinished collection method.
var ErrNotImplemented = errors.New("ListDeployments is not implemented")

// Deployment is the subset of a VCF Automation deployment used by this
// package. JSON tags follow the PageDeployment response in the reference.
type Deployment struct {
	ID        string `json:"id"`
	Name      string `json:"name"`
	ProjectID string `json:"projectId"`
	Status    string `json:"status"`
	CreatedAt string `json:"createdAt"`
}

// ListOptions contains the supported optional Get Deployments filters.
type ListOptions struct {
	Projects []string
	Status   []string
	Search   string
}

// Client calls the VCF Automation deployment collection endpoint.
type Client struct {
	baseURL  *url.URL
	token    string
	pageSize int
	http     *http.Client
}

// NewClient constructs a client. pageSize maps to the operation's size query
// parameter. A nil httpClient uses http.DefaultClient.
func NewClient(baseURL, token string, pageSize int, httpClient *http.Client) (*Client, error) {
	u, err := url.Parse(baseURL)
	if err != nil {
		return nil, fmt.Errorf("parse base URL: %w", err)
	}
	if !u.IsAbs() || u.Host == "" {
		return nil, errors.New("base URL must be absolute")
	}
	if pageSize < 1 {
		return nil, errors.New("page size must be at least 1")
	}
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{baseURL: u, token: token, pageSize: pageSize, http: httpClient}, nil
}

// ListDeployments retrieves the complete deployment collection.
func (c *Client) ListDeployments(ctx context.Context, opts ListOptions) ([]Deployment, error) {
	return nil, ErrNotImplemented
}
