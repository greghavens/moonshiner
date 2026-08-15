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

// ErrNotImplemented marks the exercise's unfinished rename workflow.
var ErrNotImplemented = errors.New("RenameDeployment is not implemented")

// ErrNameConflict reports that the requested deployment name already exists.
var ErrNameConflict = errors.New("deployment name already exists")

// Deployment is the subset of a VCF Automation deployment returned by this
// package. Its JSON tags follow the Deployment response in the reference.
type Deployment struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Description string `json:"description"`
	IconID      string `json:"iconId"`
	Status      string `json:"status"`
}

// UpdateOptions contains the optional DeploymentUpdate fields used by the
// rename workflow. Nil means unset; a non-nil pointer is an explicit value.
type UpdateOptions struct {
	Description *string
	IconID      *string
}

// Client calls the VCF Automation deployment endpoints.
type Client struct {
	baseURL *url.URL
	token   string
	http    *http.Client
}

// NewClient constructs a client. A nil httpClient uses http.DefaultClient.
func NewClient(baseURL, token string, httpClient *http.Client) (*Client, error) {
	u, err := url.Parse(baseURL)
	if err != nil {
		return nil, fmt.Errorf("parse base URL: %w", err)
	}
	if !u.IsAbs() || u.Host == "" {
		return nil, errors.New("base URL must be absolute")
	}
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{baseURL: u, token: token, http: httpClient}, nil
}

// RenameDeployment checks name availability and, only when available, updates
// the deployment.
func (c *Client) RenameDeployment(ctx context.Context, deploymentID, newName string, opts UpdateOptions) (Deployment, error) {
	return Deployment{}, ErrNotImplemented
}
