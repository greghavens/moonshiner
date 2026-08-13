// Package vcfauto is a small client for the VCF Automation deployment API in
// VMware Cloud Foundation 9.1.
//
// The one flow it supports is a gated day-2 action: read the actions that are
// currently available on a deployment, and only submit the action request when
// that precheck says the action exists and is valid for the deployment's
// current state.
package vcfauto

import (
	"context"
	"errors"
	"fmt"
	"net/http"
)

// Config configures a Client.
type Config struct {
	// BaseURL is the scheme://host[:port] root of the VCF Automation API.
	BaseURL string

	// Token is the bearer token sent on every request.
	Token string

	// HTTPClient is optional. When nil the Client uses one of its own.
	HTTPClient *http.Client
}

// Action is one entry of the action list returned by the precheck operation.
type Action struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	DisplayName string `json:"displayName"`
	ActionType  string `json:"actionType"`

	// Valid reports whether the action is valid for the deployment's current
	// state.
	Valid bool `json:"valid"`
}

// ActionRequest describes a day-2 action to submit against a deployment.
type ActionRequest struct {
	// DeploymentID is the deployment to act on. Required.
	DeploymentID string

	// ActionID is the action to perform. Required.
	ActionID string

	// Inputs is optional and is omitted from the request body when nil or empty.
	Inputs map[string]any

	// Reason is optional and is omitted from the request body when empty.
	Reason string
}

// Request is the day-2 request returned by the mutating operation.
type Request struct {
	ID           string `json:"id"`
	ActionID     string `json:"actionId"`
	DeploymentID string `json:"deploymentId"`
	Status       string `json:"status"`
}

// Errors reported by SubmitAction before or instead of the mutating call.
var (
	// ErrInvalidRequest reports an ActionRequest that is malformed, so no HTTP
	// request is made at all.
	ErrInvalidRequest = errors.New("vcfauto: invalid request")

	// ErrActionNotFound reports that the precheck did not list the action.
	ErrActionNotFound = errors.New("vcfauto: action not available on deployment")

	// ErrActionNotValid reports that the precheck listed the action but marked
	// it invalid for the deployment's current state.
	ErrActionNotValid = errors.New("vcfauto: action not valid for current deployment state")
)

// APIError reports a non-2xx response from the VCF Automation API.
type APIError struct {
	// Op is the contract operation id that produced the response.
	Op string

	StatusCode int
	Body       string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("vcfauto: %s: unexpected status %d: %s", e.Op, e.StatusCode, e.Body)
}

// Client talks to one VCF Automation endpoint.
type Client struct {
	baseURL string
	token   string
	hc      *http.Client
}

// New returns a Client for cfg.
func New(cfg Config) (*Client, error) {
	return nil, errors.New("vcfauto: New not implemented")
}

// ListActions runs the precheck read and returns the actions currently
// available on the deployment.
func (c *Client) ListActions(ctx context.Context, deploymentID string) ([]Action, error) {
	return nil, errors.New("vcfauto: ListActions not implemented")
}

// SubmitAction runs the precheck and, only if it passes, submits the day-2
// action request. When the precheck fails the mutating call is not made.
func (c *Client) SubmitAction(ctx context.Context, req ActionRequest) (*Request, error) {
	return nil, errors.New("vcfauto: SubmitAction not implemented")
}
