package vcfa

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
)

var ErrNotImplemented = errors.New("ApplyChange is not implemented")

type Client struct {
	baseURL    *url.URL
	token      string
	httpClient *http.Client
}

func NewClient(baseURL, token string, httpClient *http.Client) (*Client, error) {
	u, err := url.Parse(baseURL)
	if err != nil {
		return nil, fmt.Errorf("parse base URL: %w", err)
	}
	if u.Scheme == "" || u.Host == "" {
		return nil, errors.New("base URL must be absolute")
	}
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{baseURL: u, token: token, httpClient: httpClient}, nil
}

type DeploymentUpdate struct {
	Description *string `json:"description,omitempty"`
	IconID      *string `json:"iconId,omitempty"`
	Name        *string `json:"name,omitempty"`
}

type ActionRequest struct {
	ActionID *string        `json:"actionId,omitempty"`
	Inputs   map[string]any `json:"inputs,omitempty"`
	Reason   *string        `json:"reason,omitempty"`
}

type Change struct {
	DeploymentID     string
	ResourceID       string
	DeploymentUpdate DeploymentUpdate
	DeploymentAction ActionRequest
	ResourceAction   ActionRequest
}

type Step string

const (
	StepPatchDeployment        Step = "patch_deployment"
	StepSubmitDeploymentAction Step = "submit_deployment_action"
	StepSubmitResourceAction   Step = "submit_resource_action"
)

type StepStatus string

const (
	StepSucceeded StepStatus = "succeeded"
	StepFailed    StepStatus = "failed"
)

type StepResult struct {
	Step       Step
	Status     StepStatus
	HTTPStatus int
	ResponseID string
	Message    string
}

type ChangeReport struct {
	Steps []StepResult
}

type StepError struct {
	Step       Step
	HTTPStatus int
	Message    string
	Err        error
}

func (e *StepError) Error() string {
	if e.HTTPStatus != 0 {
		return fmt.Sprintf("%s failed with HTTP %d: %s", e.Step, e.HTTPStatus, e.Message)
	}
	return fmt.Sprintf("%s failed: %v", e.Step, e.Err)
}

func (e *StepError) Unwrap() error { return e.Err }

func (c *Client) ApplyChange(ctx context.Context, change Change) (ChangeReport, error) {
	return ChangeReport{}, ErrNotImplemented
}
