package vcfautomation

import (
	"context"
	"errors"
	"net/http"
	"time"
)

// ErrPollTimeout reports that a submitted request did not reach a terminal
// status before the poll deadline elapsed.
var ErrPollTimeout = errors.New("vcf automation: deployment request did not reach a terminal status before the poll timeout")

// Action is a deployment action reported by getDeploymentActions.
type Action struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	DisplayName string `json:"displayName"`
	Description string `json:"description"`
	ActionType  string `json:"actionType"`
	Valid       bool   `json:"valid"`
}

// ActionRequest describes a day-2 action to run against a deployment.
//
// Reason and Inputs are the optional members of the contract's
// ResourceActionRequest body. A nil pointer or nil map means the caller left
// the member unset and it must be omitted from the wire body entirely; a
// non-nil value must be sent even when it is empty.
type ActionRequest struct {
	// ActionName selects the deployment action by its ResourceAction name.
	ActionName string
	// Inputs is the optional action input object. Nil means unset.
	Inputs map[string]any
	// Reason is the optional day-2 reason. Nil means unset.
	Reason *string
}

// Request is the asynchronous deployment request returned by
// submitDeploymentActionRequest and polled with getRequest.
type Request struct {
	ID             string         `json:"id"`
	Name           string         `json:"name"`
	Status         string         `json:"status"`
	ActionID       string         `json:"actionId"`
	DeploymentID   string         `json:"deploymentId"`
	RequestedBy    string         `json:"requestedBy"`
	Details        string         `json:"details"`
	Cancelable     bool           `json:"cancelable"`
	CompletedTasks int            `json:"completedTasks"`
	TotalTasks     int            `json:"totalTasks"`
	CreatedAt      string         `json:"createdAt"`
	Outputs        map[string]any `json:"outputs"`
}

// APIError is a decoded non-success response.
type APIError struct {
	StatusCode int
	ErrorCode  string
	Message    string
}

func (e *APIError) Error() string { return "VCF Automation API request failed" }

// ProtocolError reports a malformed or contract-violating success response.
type ProtocolError struct{ Reason string }

func (e *ProtocolError) Error() string { return "VCF Automation protocol error: " + e.Reason }

// RequestFailedError reports a request that reached an unsuccessful terminal
// status. The terminal Request is still returned alongside this error.
type RequestFailedError struct {
	RequestID string
	Status    string
	Details   string
}

func (e *RequestFailedError) Error() string {
	return "VCF Automation deployment request ended in status " + e.Status
}

// Client calls the VCF Automation VM Apps Org deployment API.
type Client struct {
	// PollInterval is the delay between getRequest polls. Zero selects the
	// package default.
	PollInterval time.Duration
	// PollTimeout bounds the total time spent polling one request. Zero
	// selects the package default.
	PollTimeout time.Duration
}

// NewClient constructs a client. It is intentionally incomplete.
func NewClient(baseURL, accessToken string, httpClient *http.Client) (*Client, error) {
	return nil, errors.New("TODO: implement NewClient")
}

// RunDeploymentAction resolves the named action on the deployment, submits it,
// and polls the resulting request until it reaches a terminal status.
func (c *Client) RunDeploymentAction(ctx context.Context, deploymentID string, action ActionRequest) (*Request, error) {
	return nil, errors.New("TODO: implement RunDeploymentAction")
}
