// Package vcenter implements the focused vSphere Automation API session
// rotation client for VMware Cloud Foundation 9.0.
package vcenter

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"sync"
)

// Credential is a vCenter secret exchanged for a session token by
// Cis.Session_create.
type Credential struct {
	Username string
	Password string
}

// Filter projects the optional Vcenter.VM_list query members. A nil or empty
// member is unset and must be omitted from the request target entirely.
type Filter struct {
	VMs           []string
	Names         []string
	Folders       []string
	Datacenters   []string
	Hosts         []string
	Clusters      []string
	ResourcePools []string
	PowerStates   []string
}

// VM is the Vcenter.VM.Summary projection returned by ListVMs. CPUCount and
// MemorySizeMiB are nil when the optional response property was absent or null.
type VM struct {
	VM            string
	Name          string
	PowerState    string
	CPUCount      *int64
	MemorySizeMiB *int64
}

// APIError reports a non-success response from a focused contract operation.
type APIError struct {
	OperationID string
	StatusCode  int
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s failed with HTTP status %d", e.OperationID, e.StatusCode)
}

// ProtocolError reports a malformed or contract-violating success response.
type ProtocolError struct {
	OperationID string
	Problem     string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("%s protocol error: %s", e.OperationID, e.Problem)
}

// ErrNoSession is returned when an operation needs a live session token and the
// client does not hold one.
var ErrNoSession = errors.New("vcenter: no live session")

// ErrSessionActive is returned by Login when a session is already held.
var ErrSessionActive = errors.New("vcenter: a session is already active")

// ErrNotImplemented is returned by the exercise stub.
var ErrNotImplemented = errors.New("vcenter session rotation is not implemented")

// Client calls the three operations in the focused vCenter contract and owns
// the lifetime of the session token they share.
type Client struct {
	serviceRoot string
	httpClient  *http.Client

	mu         sync.Mutex
	credential Credential
	session    *session
}

// session tracks one server-side session token and the requests still using it.
type session struct {
	token    string
	inFlight int
	retired  bool
	drained  chan struct{}
	closeOne sync.Once
}

// NewClient constructs a focused vCenter client for a service root such as
// "https://vcenter.example.com". A nil HTTP client uses http.DefaultClient.
func NewClient(serviceRoot string, credential Credential, httpClient *http.Client) (*Client, error) {
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{
		serviceRoot: serviceRoot,
		httpClient:  httpClient,
		credential:  credential,
	}, nil
}

// Login exchanges the current credential for a session token.
func (c *Client) Login(ctx context.Context) error {
	return ErrNotImplemented
}

// ListVMs returns the virtual machines matching filter, using whichever session
// token is current when the request starts.
func (c *Client) ListVMs(ctx context.Context, filter Filter) ([]VM, error) {
	return nil, ErrNotImplemented
}

// Rotate establishes a session for next, makes it current, and retires the
// previous session only after every request that started on it has finished.
func (c *Client) Rotate(ctx context.Context, next Credential) error {
	return ErrNotImplemented
}

// Logout retires the current session once its in-flight requests have finished.
func (c *Client) Logout(ctx context.Context) error {
	return ErrNotImplemented
}
