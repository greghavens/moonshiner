// Package albdeploy implements a guarded VMware Cloud Foundation ALB deployment.
package albdeploy

import (
	"context"
	"errors"
	"net/http"
)

// ErrNotImplemented is returned by the initial scaffold.
var ErrNotImplemented = errors.New("guarded ALB deployment is not implemented")

// Config configures an SDDC Manager client.
type Config struct {
	BaseURL     string
	AccessToken string
	HTTPClient  *http.Client
}

// DeployOptions controls optional query parameters shared by the precheck and
// deployment operations.
type DeployOptions struct {
	SkipCompatibilityCheck *bool
}

// AlbControllerNodeSpec is the contract model for one ALB controller node.
type AlbControllerNodeSpec struct {
	IPAddress string `json:"ipAddress"`
}

// AlbControllerClusterSpec is the request model shared by the validation and
// deployment operations.
type AlbControllerClusterSpec struct {
	NSXIDs              []string                 `json:"nsxIds"`
	ClusterName         string                   `json:"clusterName"`
	ClusterFQDN         string                   `json:"clusterFqdn"`
	FormFactor          string                   `json:"formFactor"`
	AdminPassword       string                   `json:"adminPassword"`
	Nodes               *[]AlbControllerNodeSpec `json:"nodes,omitempty"`
	BundleID            string                   `json:"bundleId"`
	VCFOpsAdminPassword *string                  `json:"vcfopsAdminPassword,omitempty"`
}

// Validation is the precheck response used to gate deployment.
type Validation struct {
	ID              string `json:"id"`
	Description     string `json:"description"`
	ExecutionStatus string `json:"executionStatus"`
	ResultStatus    string `json:"resultStatus"`
}

// Task is the accepted deployment task.
type Task struct {
	ID                string `json:"id"`
	Name              string `json:"name"`
	Status            string `json:"status"`
	CreationTimestamp string `json:"creationTimestamp"`
}

// Result reports the precheck and any accepted deployment task.
type Result struct {
	Validation Validation
	Task       *Task
	Deployed   bool
}

// APIError represents a non-success SDDC Manager response.
type APIError struct {
	OperationID        string
	StatusCode         int
	ErrorCode          string
	Message            string
	RemediationMessage string
	ReferenceToken     string
}

func (e *APIError) Error() string {
	return "SDDC Manager API request failed"
}

// TransportError represents a request transport failure.
type TransportError struct {
	OperationID string
}

func (e *TransportError) Error() string {
	return "SDDC Manager transport request failed"
}

// PrecheckError reports a contract-valid precheck result that blocks mutation.
type PrecheckError struct {
	Validation Validation
}

func (e *PrecheckError) Error() string {
	return "ALB deployment blocked by precheck"
}

// ProtocolError reports a malformed contract success response.
type ProtocolError struct {
	OperationID string
	Reason      string
}

func (e *ProtocolError) Error() string {
	return "SDDC Manager success response violated the contract"
}

// Client performs a guarded ALB deployment.
type Client struct{}

// NewClient validates configuration without performing network I/O.
func NewClient(config Config) (*Client, error) {
	return nil, ErrNotImplemented
}

// DeployALBCluster validates the request and invokes the deployment mutation
// only when the precheck completes successfully.
func (c *Client) DeployALBCluster(
	ctx context.Context,
	spec AlbControllerClusterSpec,
	options DeployOptions,
) (Result, error) {
	return Result{}, ErrNotImplemented
}
