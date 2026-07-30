// Package sessionrotation implements a focused, contract-pinned vCenter client.
package sessionrotation

import (
	"errors"
	"fmt"
	"net/http"
	"sync"
)

const (
	SessionCreateOperation = "Cis.Session_create"
	VMListOperation        = "Vcenter.VM_list"
	SessionDeleteOperation = "Cis.Session_delete"
)

// ErrNotImplemented is returned by the incomplete client scaffold.
var ErrNotImplemented = errors.New("vCenter session rotation is not implemented")

// Config configures one focused vSphere Automation API client.
type Config struct {
	BaseURL    string
	Username   string
	Password   string
	HTTPClient *http.Client
}

// ListOptions is the focused Vcenter.VM_list filter projection.
// A nil slice is unset. A non-nil slice must contain unique nonblank values.
type ListOptions struct {
	VMs           []string
	Names         []string
	Folders       []string
	Datacenters   []string
	Hosts         []string
	Clusters      []string
	ResourcePools []string
	PowerStates   []string
}

// VMSummary is the focused Vcenter.VM.Summary response projection.
type VMSummary struct {
	VM            string `json:"vm"`
	Name          string `json:"name"`
	PowerState    string `json:"power_state"`
	CPUCount      *int64 `json:"cpu_count,omitempty"`
	MemorySizeMiB *int64 `json:"memory_size_mib,omitempty"`
}

// ValidationError reports invalid local input.
type ValidationError struct {
	Field string
}

func (e *ValidationError) Error() string {
	return e.Field + " is invalid"
}

// APIError represents a non-success HTTP response.
type APIError struct {
	OperationID string
	StatusCode  int
	Payload     any
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s failed with HTTP status %d", e.OperationID, e.StatusCode)
}

// ProtocolError reports a successful response that violates the focused
// contract.
type ProtocolError struct {
	OperationID string
	Reason      string
}

func (e *ProtocolError) Error() string {
	return e.OperationID + " response violated the focused vCenter contract"
}

// TransportError reports a request failure without exposing transport text.
type TransportError struct {
	OperationID string
	cause       error
}

func (e *TransportError) Error() string {
	return e.OperationID + " transport failure"
}

func (e *TransportError) Unwrap() error {
	return e.cause
}

type sessionGeneration struct {
	token    string
	inFlight int
}

// Client pins every request to the session generation captured at call start.
type Client struct {
	baseURL    string
	username   string
	httpClient *http.Client

	mu         sync.Mutex
	condition  *sync.Cond
	rotationMu sync.Mutex
	active     *sessionGeneration
	closed     bool
	closeErr   error
}

// currentTokenForTest gives the protected same-package verifier a
// deterministic publication barrier without adding a public test hook.
func (c *Client) currentTokenForTest() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.active == nil {
		return ""
	}
	return c.active.token
}
