// Package vcfautomation implements the small VCF Automation REST flow described
// by docs/contract.json.
package vcfautomation

import (
	"context"
	"net/http"
	"time"
)

// ActionRequest is the request body accepted by Submit Deployment Action
// Request. All fields are optional in the official reference documentation.
type ActionRequest struct {
	ActionID string         `json:"actionId,omitempty"`
	Inputs   map[string]any `json:"inputs,omitempty"`
	Reason   string         `json:"reason,omitempty"`
}

// Request contains the fields needed to follow an asynchronous deployment
// request. Additional response fields are intentionally ignored by encoding/json.
type Request struct {
	ID             string         `json:"id,omitempty"`
	ActionID       string         `json:"actionId,omitempty"`
	DeploymentID   string         `json:"deploymentId,omitempty"`
	Inputs         map[string]any `json:"inputs,omitempty"`
	Outputs        map[string]any `json:"outputs,omitempty"`
	Name           string         `json:"name,omitempty"`
	RequestedBy    string         `json:"requestedBy,omitempty"`
	Status         string         `json:"status,omitempty"`
	CompletedTasks int            `json:"completedTasks"`
	TotalTasks     int            `json:"totalTasks"`
}

// Client is a client for the two operations pinned in docs/contract.json.
type Client struct {
	baseURL      string
	token        string
	httpClient   *http.Client
	pollInterval time.Duration
}

// NewClient constructs a Client. baseURL is the API origin, without an API
// path. A nil httpClient uses http.DefaultClient. Non-positive poll intervals
// cause polls to proceed without an artificial delay.
func NewClient(baseURL, token string, httpClient *http.Client, pollInterval time.Duration) *Client {
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{
		baseURL:      baseURL,
		token:        token,
		httpClient:   httpClient,
		pollInterval: pollInterval,
	}
}

// SubmitDeploymentActionAndWait submits a day-two deployment action and waits
// for the resulting asynchronous request to reach a terminal state.
func (c *Client) SubmitDeploymentActionAndWait(ctx context.Context, deploymentID string, action ActionRequest) (Request, error) {
	// TODO: implement the two-operation asynchronous flow in docs/contract.json.
	panic("not implemented")
}
