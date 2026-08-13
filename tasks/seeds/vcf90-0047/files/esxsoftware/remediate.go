// Package esxsoftware remediates an ESXi cluster against its desired software
// document through the vSphere Automation API of a VMware Cloud Foundation 9.0
// vCenter Server.
//
// Applying a desired software document is not a request that finishes when its
// response arrives. The apply operation answers 202 with the identifier of a
// com.vmware.cis.task, and the remediation itself continues on the appliance
// long afterwards: hosts enter maintenance mode one at a time, reboot, and
// rejoin. The caller learns the outcome only by polling that task until it
// reaches a terminal state. Treating the 202 as completion reports success for
// work that has not happened yet and may still fail.
//
// See docs/contract.json for the pinned wire contract and
// docs/official_sources.json for its provenance.
package esxsoftware

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"time"
)

// Status is a Cis.Task.Status value.
type Status string

// The task statuses the specification defines. PENDING, RUNNING and BLOCKED are
// nonterminal; SUCCEEDED and FAILED are terminal.
const (
	StatusPending   Status = "PENDING"
	StatusRunning   Status = "RUNNING"
	StatusBlocked   Status = "BLOCKED"
	StatusSucceeded Status = "SUCCEEDED"
	StatusFailed    Status = "FAILED"
)

// DefaultPollInterval is used when PollOptions.Interval is not positive.
const DefaultPollInterval = 5 * time.Second

// ApplySpec is the Esx.Settings.Clusters.Software.ApplySpec request body. Every
// member is optional, and an unset member must not reach the wire in any form.
type ApplySpec struct {
	// Commit is the minimum desired-state commit to apply. Empty means unset.
	Commit string
	// Hosts limits remediation to these hosts. Nil or empty means unset, which
	// the appliance reads as "remediate every host in the cluster".
	Hosts []string
	// AcceptEULA accepts the VMware End User License Agreement. Nil means unset;
	// a pointer to false is a value the caller chose and must be sent.
	AcceptEULA *bool
}

// GetSpec is the Cis.Tasks.GetSpec query object. It is an exploded form
// parameter: each set member becomes its own query parameter. Nil means unset.
type GetSpec struct {
	ReturnAll     *bool
	ExcludeResult *bool
}

// Message is a Vapi.Std.LocalizableMessage.
type Message struct {
	ID             string
	DefaultMessage string
	Args           []string
}

// Progress is a Cis.Task.Progress.
type Progress struct {
	Total     int64
	Completed int64
	Message   Message
}

// TaskInfo is a Cis.Task.Info.
type TaskInfo struct {
	Status      Status
	Cancelable  bool
	Service     string
	Operation   string
	Description Message
	Parent      string
	User        string
	StartTime   string
	EndTime     string
	// Progress is nil when the appliance reported none.
	Progress *Progress
	// Result and Error are carried through undecoded; their shape is
	// operation-specific. Both are nil when absent.
	Result json.RawMessage
	Error  json.RawMessage
}

// Report describes how far a remediation got. ApplyAndAwait always returns a
// populated Report, including alongside a non-nil error.
type Report struct {
	// TaskID is the identifier the apply operation handed back, empty if the
	// apply never succeeded.
	TaskID string
	// Status is the last status observed, empty if the task was never polled.
	Status Status
	// StatusSequence records every status observed, in order, one entry per
	// completed poll.
	StatusSequence []Status
	// Polls counts completed Cis.Tasks_get calls.
	Polls int
	// Info is the task information from the last completed poll, nil if none.
	Info *TaskInfo
	// Succeeded is true only when the task settled SUCCEEDED.
	Succeeded bool
}

// APIError reports a response status the contract does not list as success.
type APIError struct {
	OperationID string
	StatusCode  int
	ErrorType   string
	Messages    []Message
}

func (e *APIError) Error() string {
	return "esxsoftware: api error"
}

// TaskFailedError reports a task that settled in an unsuccessful terminal
// state. Every HTTP call succeeded; the remediation did not.
type TaskFailedError struct {
	TaskID    string
	Status    Status
	ErrorType string
	Messages  []Message
	Info      *TaskInfo
}

func (e *TaskFailedError) Error() string {
	return "esxsoftware: task failed"
}

// ProtocolError reports a response that was accepted at the HTTP layer but does
// not match the contract, such as an unknown task status.
type ProtocolError struct {
	OperationID string
	Detail      string
}

func (e *ProtocolError) Error() string {
	return "esxsoftware: protocol error"
}

// PollOptions tunes how ApplyAndAwait polls the accepted task.
type PollOptions struct {
	// Interval is the wait between polls; values <= 0 mean DefaultPollInterval.
	Interval time.Duration
	// GetSpec is passed to every Cis.Tasks_get call.
	GetSpec GetSpec
}

// Client talks to one vCenter Server.
type Client struct {
	baseURL    string
	sessionID  string
	httpClient *http.Client
}

// NewClient builds a client for a vCenter Server service root such as
// "https://vc-a01.vcf.local". A nil httpClient means http.DefaultClient.
func NewClient(baseURL, sessionID string, httpClient *http.Client) (*Client, error) {
	return nil, errors.New("esxsoftware: NewClient is not implemented")
}

// ApplySoftware invokes Esx.Settings.Clusters.Software_apply$Task and returns
// the identifier of the accepted task. It does not wait for the task.
func (c *Client) ApplySoftware(ctx context.Context, cluster string, spec ApplySpec) (string, error) {
	return "", errors.New("esxsoftware: ApplySoftware is not implemented")
}

// GetTask invokes Cis.Tasks_get once for the given task identifier.
func (c *Client) GetTask(ctx context.Context, taskID string, spec GetSpec) (*TaskInfo, error) {
	return nil, errors.New("esxsoftware: GetTask is not implemented")
}

// ApplyAndAwait applies the desired software document to the cluster and polls
// the accepted task until it reaches a terminal state.
func (c *Client) ApplyAndAwait(ctx context.Context, cluster string, spec ApplySpec, opts PollOptions) (Report, error) {
	return Report{}, errors.New("esxsoftware: ApplyAndAwait is not implemented")
}
