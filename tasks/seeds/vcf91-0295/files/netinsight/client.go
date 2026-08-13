// Package netinsight is a client for the VCF Operations for Networks 9.1 API.
//
// Everything this package puts on the wire is pinned by docs/contract.json.
package netinsight

import (
	"context"
	"errors"
	"net/http"
	"time"
)

// Domain selects the authentication domain for a credential. A zero Domain is
// not the same as no domain at all: callers signal "no domain" by leaving
// Credentials.Domain nil.
type Domain struct {
	// Type is the domain type, "LDAP" or "LOCAL".
	Type string
	// Value is the domain value. It is not required for a LOCAL domain.
	Value string
}

// Credentials authenticates a user against the appliance.
type Credentials struct {
	Username string
	Password string
	// Domain is optional.
	Domain *Domain
}

// Token is an issued auth token.
type Token struct {
	Value  string
	Expiry int64
}

// SaveRequest asks the appliance to save a batch of discovered applications.
type SaveRequest struct {
	// SourceEntityIDs are the discovered application ids to save.
	SourceEntityIDs []string
	// DiscoveryType is optional; an empty string means the caller did not set it.
	DiscoveryType string
	// EnableIntent is optional; nil means the caller did not set it. A non-nil
	// pointer to false is a caller decision, not an absent value.
	EnableIntent *bool
}

// AppSaveResult reports the outcome for one application in the batch.
type AppSaveResult struct {
	EntityID     string
	Name         string
	ResponseCode string
	ErrorMessage string
}

// TaskProgress is one progress report for a bulk save task.
type TaskProgress struct {
	RequestID  string
	TaskName   string
	Status     string
	Progress   float64
	StartTime  int64
	AppResults []AppSaveResult
}

// APIError is a non-success response from the appliance.
type APIError struct {
	Operation  string
	StatusCode int
	Code       int
	Message    string
}

func (e *APIError) Error() string {
	return "netinsight: " + e.Operation + ": unimplemented"
}

// TaskFailedError is returned when a task reaches a terminal failure state.
type TaskFailedError struct {
	Progress TaskProgress
}

func (e *TaskFailedError) Error() string {
	return "netinsight: task " + e.Progress.RequestID + " ended in " + e.Progress.Status
}

// errNotImplemented is returned by every stub below.
var errNotImplemented = errors.New("netinsight: not implemented")

// Client talks to one VCF Operations for Networks appliance.
type Client struct {
	baseURL string
	http    *http.Client
	token   string
}

// NewClient returns a client for the appliance root baseURL, for example
// "https://vcfon.example.com". The contract base path is appended by the
// client, so baseURL must not already carry it. A nil hc means http.DefaultClient.
func NewClient(baseURL string, hc *http.Client) (*Client, error) {
	return nil, errNotImplemented
}

// CreateToken authenticates and stores the issued token on the client for use
// by every subsequent authenticated call.
func (c *Client) CreateToken(ctx context.Context, cred Credentials) (Token, error) {
	return Token{}, errNotImplemented
}

// DeleteToken deletes the token currently held by the client and forgets it.
func (c *Client) DeleteToken(ctx context.Context) error {
	return errNotImplemented
}

// SaveDiscoveredApplications submits the batch and returns the request id of
// the task the appliance created. The batch is not saved when this returns.
func (c *Client) SaveDiscoveredApplications(ctx context.Context, req SaveRequest) (string, error) {
	return "", errNotImplemented
}

// GetTaskProgress fetches one progress report for a request id.
func (c *Client) GetTaskProgress(ctx context.Context, requestID string) (TaskProgress, error) {
	return TaskProgress{}, errNotImplemented
}

// SaveAndWait submits the batch and polls its task until the task reaches a
// terminal state, returning the terminal report.
func (c *Client) SaveAndWait(ctx context.Context, req SaveRequest, pollInterval time.Duration) (TaskProgress, error) {
	return TaskProgress{}, errNotImplemented
}
