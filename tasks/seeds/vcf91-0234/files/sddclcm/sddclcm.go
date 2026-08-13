// Package sddclcm is a small client for the two VCF 9.1 SDDC LCM operations
// pinned in docs/contract.json: performComponentAction (action=apply) and
// getTask.
//
// Applying a component upgrade mutates the fleet, so the submission is keyed by
// a caller-supplied correlation id and must be replayable: re-sending it after
// a lost or failed response has to converge on the same upgrade rather than
// starting a second one.
package sddclcm

import (
	"context"
	"errors"
	"net/http"
	"time"
)

// ErrMissingCorrelationID is returned when UpgradeSpec.CorrelationID is empty.
// The client refuses to submit, because a submission without a stable
// correlation id cannot be retried safely.
var ErrMissingCorrelationID = errors.New("sddclcm: UpgradeSpec.CorrelationID is required")

// ErrPollTimeout is returned when a task has not reached a terminal status
// within the caller's budget.
var ErrPollTimeout = errors.New("sddclcm: timed out waiting for a terminal task status")

// APIError reports a non-2xx response. Code, ReferenceID and Message are taken
// from the ErrorResponse body when the service sent one.
type APIError struct {
	StatusCode  int
	Code        string
	ReferenceID string
	Message     string
	Body        []byte
}

func (e *APIError) Error() string { return "" }

// ProtocolError reports a malformed success response or one that the pinned
// contract does not allow.
type ProtocolError struct {
	Reason string
}

func (e *ProtocolError) Error() string { return "" }

// TaskFailedError reports a task that reached a terminal non-success status.
type TaskFailedError struct {
	Task *Task
}

func (e *TaskFailedError) Error() string { return "" }

// SoftwareSpec is the desired post-upgrade software state.
type SoftwareSpec struct {
	Version string
}

// DepotSpec locates the upgrade manifest. Certificate is optional.
type DepotSpec struct {
	URL         string
	Certificate []string
}

// ComponentDesiredSpec is the component's desired configuration. Software and
// Depot are required; Policy, UserInput and AdditionalInput are optional.
type ComponentDesiredSpec struct {
	Software        SoftwareSpec
	Policy          map[string]any
	Depot           DepotSpec
	UserInput       map[string]any
	AdditionalInput map[string]any
}

// LcmPlatformSpec carries lifecycle options for the upgrade itself.
// PerformBackup is required whenever the object is present.
type LcmPlatformSpec struct {
	PerformBackup bool
}

// UpgradeSpec is the ComponentUpgradeSpec request payload. LcmPlatform is
// optional; CorrelationID is required by this client.
type UpgradeSpec struct {
	ComponentSpec ComponentDesiredSpec
	LcmPlatform   *LcmPlatformSpec
	CorrelationID string
}

// LocalizableMessage mirrors the spec schema of the same name.
type LocalizableMessage struct {
	ID               string            `json:"id,omitempty"`
	DefaultMessage   string            `json:"defaultMessage,omitempty"`
	LocalizedMessage string            `json:"localizedMessage,omitempty"`
	Args             map[string]string `json:"args,omitempty"`
}

// Task mirrors the fields of the spec Task schema this client needs.
type Task struct {
	ID            string              `json:"id"`
	Name          string              `json:"name,omitempty"`
	Description   *LocalizableMessage `json:"description,omitempty"`
	Status        string              `json:"status,omitempty"`
	Type          string              `json:"type,omitempty"`
	CreatedBy     string              `json:"createdBy,omitempty"`
	UpdatedBy     string              `json:"updatedBy,omitempty"`
	ResourceID    string              `json:"resourceId,omitempty"`
	ResourceType  string              `json:"resourceType,omitempty"`
	CreateTime    string              `json:"createTime,omitempty"`
	StartTime     string              `json:"startTime,omitempty"`
	UpdateTime    string              `json:"updateTime,omitempty"`
	EndTime       string              `json:"endTime,omitempty"`
	CorrelationID string              `json:"correlationId,omitempty"`
	ParentTaskID  string              `json:"parentTaskId,omitempty"`
	Retriable     bool                `json:"retriable,omitempty"`
	Cancellable   bool                `json:"cancellable,omitempty"`
}

// Option customises a Client.
type Option func(*Client)

// WithHTTPClient overrides the transport used for every request.
func WithHTTPClient(h *http.Client) Option {
	return func(c *Client) { _ = h }
}

// WithMaxAttempts caps how many times a single submission is sent, including
// the first attempt. The default is 4.
func WithMaxAttempts(n int) Option {
	return func(c *Client) { _ = n }
}

// WithRetryBackoff sets the pause before attempt n (1-based, so backoff(1) runs
// before the second attempt). The default is 250ms per attempt.
func WithRetryBackoff(f func(attempt int) time.Duration) Option {
	return func(c *Client) { _ = f }
}

// WithPollInterval sets the pause between getTask polls. The default is 2s.
func WithPollInterval(d time.Duration) Option {
	return func(c *Client) { _ = d }
}

// Client talks to one SDDC LCM service endpoint. It is safe for concurrent use
// by multiple goroutines.
type Client struct {
	baseURL string
	token   string
}

// NewClient builds a client for baseURL authenticating with the given bearer
// token. It rejects an empty baseURL or token.
func NewClient(baseURL, bearerToken string, opts ...Option) (*Client, error) {
	return nil, errors.New("sddclcm: NewClient is not implemented")
}

// ApplyComponentUpgrade submits performComponentAction with action=apply and
// returns the accepted Task. Retries re-send byte-identical bytes under the
// same correlation id so the service can collapse them into one upgrade.
func (c *Client) ApplyComponentUpgrade(ctx context.Context, componentID string, spec UpgradeSpec) (*Task, error) {
	return nil, errors.New("sddclcm: ApplyComponentUpgrade is not implemented")
}

// GetTask reads one task by id.
func (c *Client) GetTask(ctx context.Context, taskID string) (*Task, error) {
	return nil, errors.New("sddclcm: GetTask is not implemented")
}

// ApplyUpgradeAndWait submits the upgrade and polls until the task reaches a
// terminal status or timeout elapses.
func (c *Client) ApplyUpgradeAndWait(ctx context.Context, componentID string, spec UpgradeSpec, timeout time.Duration) (*Task, error) {
	return nil, errors.New("sddclcm: ApplyUpgradeAndWait is not implemented")
}
