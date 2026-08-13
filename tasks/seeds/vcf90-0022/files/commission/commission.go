// Package commission drives the SDDC Manager host-commissioning workflow:
// submit a precheck, wait for it to finish, and commission the hosts only if
// the precheck succeeded.
//
// The wire contract this package must satisfy is docs/contract.json, derived
// from the VMware Cloud Foundation 9.0 SDDC Manager OpenAPI specification.
//
// TODO: this package is a stub. Implement Client.CommissionHosts.
package commission

import (
	"context"
	"errors"
	"net/http"
	"time"
)

// ErrPrecheckFailed reports that the precheck completed with a non-SUCCEEDED
// result, so no host was commissioned.
var ErrPrecheckFailed = errors.New("commission: host prechecks failed")

// ErrUnsupportedStorageType reports a storage type that the 9.0 contract does
// not accept. It is returned before any request is issued.
var ErrUnsupportedStorageType = errors.New("commission: unsupported storage type")

// HostSpec is one host to commission. It mirrors the HostCommissionSpec schema.
//
// FQDN, Username, Password, StorageType and NetworkPoolID are required. The
// remaining fields are optional: when left empty they must be absent from the
// serialized request, not sent as empty strings.
type HostSpec struct {
	FQDN          string
	Username      string
	Password      string
	StorageType   string
	NetworkPoolID string

	VvolStorageProtocolType string
	NetworkPoolName         string
	SSHThumbprint           string
	SSLThumbprint           string
}

// HostPrecheckError is a per-host precheck failure reported by SDDC Manager.
type HostPrecheckError struct {
	FQDN   string
	Result string
	Error  string
}

// Result reports what the workflow did.
type Result struct {
	// PrecheckID is the execution id returned by the precheck submission.
	PrecheckID string
	// PrecheckResult is the completed precheck result, "SUCCEEDED" or "FAILED".
	PrecheckResult string
	// Polls counts the precheck status reads performed.
	Polls int
	// Committed reports whether the commissioning call was issued. It is false
	// whenever the precheck did not succeed.
	Committed bool
	// TaskID is the id of the Task returned by the commissioning call. It is
	// empty unless Committed is true.
	TaskID string
	// HostErrors carries one entry per host whose precheck did not succeed, in
	// the order SDDC Manager reported them. It is empty when every host passed.
	HostErrors []HostPrecheckError
}

// Client talks to one SDDC Manager instance.
type Client struct {
	baseURL string
	httpc   *http.Client

	// PollInterval is the delay between precheck status reads. Zero uses
	// DefaultPollInterval.
	PollInterval time.Duration
	// MaxPolls bounds the number of precheck status reads before the workflow
	// gives up. Zero uses DefaultMaxPolls.
	MaxPolls int
}

// Defaults for Client.PollInterval and Client.MaxPolls.
const (
	DefaultPollInterval = 2 * time.Second
	DefaultMaxPolls     = 150
)

// NewClient returns a Client for the SDDC Manager at baseURL. A nil httpc uses
// a default client.
func NewClient(baseURL string, httpc *http.Client) *Client {
	if httpc == nil {
		httpc = &http.Client{}
	}
	return &Client{baseURL: baseURL, httpc: httpc}
}

// CommissionHosts runs the precheck-gated commissioning workflow:
//
//  1. submit the hosts for prechecking,
//  2. read the precheck status until it reports completion,
//  3. commission the hosts only when the completed result is SUCCEEDED.
//
// When the precheck fails it returns a Result with Committed false and an error
// wrapping ErrPrecheckFailed, having issued no commissioning request.
func (c *Client) CommissionHosts(ctx context.Context, specs []HostSpec) (*Result, error) {
	return nil, errors.New("commission: not implemented")
}
