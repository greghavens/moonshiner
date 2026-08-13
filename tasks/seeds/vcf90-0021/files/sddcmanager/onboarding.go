// Package sddcmanager onboards new capacity into a VMware Cloud Foundation 9.0
// deployment through the SDDC Manager REST API.
//
// The onboarding sequence is a multi-step change: create a network pool, widen
// one of its networks with an IP range, then commission ESXi hosts against that
// pool and wait for the resulting task. Steps that already ran are not undone
// when a later step fails, so Onboard must always hand back a Report that says
// exactly how far the change got and what it left behind.
//
// See docs/contract.json for the wire contract and docs/official_sources.json
// for its provenance.
package sddcmanager

import (
	"context"
	"errors"
	"net/http"
	"time"
)

// NetworkSpec is one network of a new network pool.
type NetworkSpec struct {
	Type    string
	VlanID  int32
	MTU     int32
	Subnet  string
	Mask    string
	Gateway string
}

// IPRange is an inclusive IPv4 range added to an existing network.
type IPRange struct {
	Start string
	End   string
}

// HostSpec describes one ESXi host to commission. A nil pointer field is an
// unset optional value and must not reach the wire in any form.
type HostSpec struct {
	FQDN        string
	Username    string
	Password    string
	StorageType string

	VvolStorageProtocolType *string
	SSHThumbprint           *string
	SSLThumbprint           *string
}

// Plan is the whole change to apply.
type Plan struct {
	NetworkPoolName string
	Networks        []NetworkSpec

	// IPRangeNetworkType names which network of the freshly created pool the
	// IP range is added to, matched against NetworkSpec.Type.
	IPRangeNetworkType string
	IPRange            IPRange

	Hosts []HostSpec
}

// StepStatus is the outcome of a single onboarding step.
type StepStatus string

const (
	StepSucceeded StepStatus = "SUCCEEDED"
	StepFailed    StepStatus = "FAILED"
	StepSkipped   StepStatus = "SKIPPED"
)

// StepReport records one step of the sequence, named by its specification
// operationId.
type StepReport struct {
	OperationID string
	Status      StepStatus
	Detail      string
}

// HostOutcome is the per-host result read out of the commission task.
type HostOutcome struct {
	FQDN      string
	Status    string
	ErrorCode string
	Message   string
}

// Report is the account of a change attempt. It is filled in as far as the
// sequence got, whether or not Onboard returns an error.
type Report struct {
	Steps []StepReport

	NetworkPoolCreated bool
	NetworkPoolID      string
	NetworkID          string
	IPRangeAdded       bool

	TaskID     string
	TaskStatus string
	Hosts      []HostOutcome

	// PersistedResources names everything the attempt left behind on the
	// appliance, so an operator can reconcile after a partial failure.
	PersistedResources []string

	Succeeded bool
}

// APIError is a non-success HTTP status from one of the contract operations.
type APIError struct {
	OperationID string
	StatusCode  int
	ErrorCode   string
	Message     string
}

func (e *APIError) Error() string {
	return "sddcmanager: " + e.OperationID + ": " + http.StatusText(e.StatusCode) + ": " + e.ErrorCode + ": " + e.Message
}

// CommissionFailedError reports a commission task that reached a terminal state
// other than SUCCESSFUL. The HTTP calls all succeeded; the change did not.
type CommissionFailedError struct {
	TaskID     string
	TaskStatus string
	ErrorCode  string
	Message    string
	Hosts      []HostOutcome
}

func (e *CommissionFailedError) Error() string {
	return "sddcmanager: commission task " + e.TaskID + " ended " + e.TaskStatus + ": " + e.ErrorCode + ": " + e.Message
}

// Client talks to one SDDC Manager appliance.
type Client struct {
	baseURL     string
	accessToken string
	httpClient  *http.Client
}

// NewClient builds a client for the SDDC Manager service root. A nil
// httpClient means http.DefaultClient.
func NewClient(baseURL, accessToken string, httpClient *http.Client) (*Client, error) {
	return nil, errors.New("sddcmanager: NewClient is not implemented")
}

// Onboard applies plan and waits for the commission task to settle. The
// returned Report describes every step that ran, and is populated even when the
// error is non-nil.
func (c *Client) Onboard(ctx context.Context, plan Plan, pollInterval time.Duration) (Report, error) {
	return Report{}, errors.New("sddcmanager: Onboard is not implemented")
}

var _ = time.Second
