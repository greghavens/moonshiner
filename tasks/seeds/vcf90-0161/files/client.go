package vcfautomation

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"strings"
)

type Client struct {
	baseURL    string
	token      string
	httpClient *http.Client
}

type DeploymentUpdate struct {
	Description *string
	IconID      *string
	Name        *string
}

type Deployment struct {
	ID          string `json:"id"`
	Description string `json:"description"`
	IconID      string `json:"iconId"`
	Name        string `json:"name"`
}

func NewClient(baseURL, token string, httpClient *http.Client) (*Client, error) {
	parsed, err := url.Parse(baseURL)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return nil, errors.New("vcfautomation: base URL must be an absolute URL")
	}
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{
		baseURL:    strings.TrimRight(baseURL, "/"),
		token:      token,
		httpClient: httpClient,
	}, nil
}

func String(value string) *string {
	return &value
}

// PatchDeployment applies absolute deployment metadata values. It is safe for a
// caller to invoke again with the same update after an uncertain outcome.
func (c *Client) PatchDeployment(ctx context.Context, deploymentID string, update DeploymentUpdate) (Deployment, error) {
	return Deployment{}, errors.New("vcfautomation: PatchDeployment is not implemented")
}
