// Package cpusweep implements the focused vSphere Automation API CPU
// right-sizing sweep described by the protected OpenAPI-derived contract.
package cpusweep

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
)

const maxResponseBytes = 1 << 20

// Operation identifiers named by the focused contract.
const (
	OpSessionCreate = "Cis.Session_create"
	OpVMList        = "Vcenter.VM_list"
	OpCPUGet        = "Vcenter.Vm.Hardware.Cpu_get"
	OpCPUUpdate     = "Vcenter.Vm.Hardware.Cpu_update"
)

// basePath is the server base path declared by the contract's server URL.
const basePath = "/api"

// sessionHeader is the api_key_auth header named by the contract.
const sessionHeader = "vmware-api-session-id"

// ErrNotImplemented is returned by the incomplete workflow.
var ErrNotImplemented = errors.New("cpu sweep workflow is not implemented")

// ReauthFunc is invoked immediately before each session refresh. It is never
// invoked for the initial login.
type ReauthFunc func(
	ctx context.Context,
	operationID string,
	refreshCount int,
) error

// Config configures a Client.
type Config struct {
	BaseURL    string
	Username   string
	Password   string
	HTTPClient *http.Client
	// MaxReauth bounds how many times one sweep may re-run Cis.Session_create
	// after its initial login.
	MaxReauth int
	OnReauth  ReauthFunc
}

// Desired holds the target CPU settings. A nil member is not managed by the
// sweep and is never sent.
type Desired struct {
	Count            *int64
	CoresPerSocket   *int64
	HotAddEnabled    *bool
	HotRemoveEnabled *bool
}

// SweepRequest selects the virtual machines to right-size. A nil or empty
// filter slice means the filter is unset and is not sent.
type SweepRequest struct {
	Names       []string
	PowerStates []string
	Desired     Desired
}

// VMSummary is the focused Vcenter.VM.Summary projection.
type VMSummary struct {
	VM            string `json:"vm"`
	Name          string `json:"name"`
	PowerState    string `json:"power_state"`
	CPUCount      *int64 `json:"cpu_count,omitempty"`
	MemorySizeMiB *int64 `json:"memory_size_mib,omitempty"`
}

// CPUInfo is the focused Vcenter.Vm.Hardware.Cpu.Info projection.
type CPUInfo struct {
	Count            int64 `json:"count"`
	CoresPerSocket   int64 `json:"cores_per_socket"`
	HotAddEnabled    bool  `json:"hot_add_enabled"`
	HotRemoveEnabled bool  `json:"hot_remove_enabled"`
}

// Outcome records what the sweep decided for one virtual machine.
type Outcome struct {
	VM         string
	Name       string
	PowerState string
	Before     CPUInfo
	// Changed lists the UpdateSpec members actually sent, in contract order.
	Changed []string
	// Deferred lists members that differ but may not be modified while the
	// virtual machine is not powered off.
	Deferred []string
	Updated  bool
}

// SweepResult is the outcome of a sweep. Outcomes holds every virtual machine
// completed before the sweep returned, including when it returned an error.
type SweepResult struct {
	Outcomes []Outcome
	Reauths  int
}

// APIError preserves a structured non-success response without exposing its
// body, decoded messages, or authentication challenge through Error.
type APIError struct {
	OperationID string
	Status      int
	ErrorType   string
	Messages    []string
	Challenge   string
}

func (e *APIError) Error() string {
	if e == nil {
		return "vCenter API request failed"
	}
	return fmt.Sprintf(
		"vCenter operation %s failed with HTTP %d",
		e.OperationID,
		e.Status,
	)
}

// TransportError wraps a transport failure while keeping its text redacted.
type TransportError struct {
	OperationID string
	Err         error
}

func (e *TransportError) Error() string {
	if e == nil {
		return "vCenter transport failed"
	}
	return fmt.Sprintf("vCenter operation %s transport failed", e.OperationID)
}

func (e *TransportError) Unwrap() error {
	if e == nil {
		return nil
	}
	return e.Err
}

// ProtocolError reports a success response that violated the contract.
type ProtocolError struct {
	OperationID string
	Reason      string
}

func (e *ProtocolError) Error() string {
	if e == nil {
		return "vCenter protocol error"
	}
	return fmt.Sprintf(
		"vCenter operation %s violated the response contract: %s",
		e.OperationID,
		e.Reason,
	)
}

// ReauthLimitError reports exhaustion of the session refresh budget.
type ReauthLimitError struct {
	OperationID string
	Limit       int
}

func (e *ReauthLimitError) Error() string {
	if e == nil {
		return "vCenter session refresh limit reached"
	}
	return fmt.Sprintf(
		"vCenter operation %s needed a session refresh beyond the limit of %d",
		e.OperationID,
		e.Limit,
	)
}

// Client is a focused, stateless client for the four contract operations. It
// is safe for concurrent use; each sweep owns its own session.
type Client struct {
	baseURL    string
	username   string
	password   string
	httpClient *http.Client
	maxReauth  int
	onReauth   ReauthFunc
}

// session is the per-sweep authentication state.
type session struct {
	token   string
	reauths int
}

// apiRequest is one contract-targeted request that can be replayed verbatim
// after a session refresh.
type apiRequest struct {
	operationID   string
	method        string
	path          string
	rawQuery      string
	body          []byte
	successStatus int
	basicAuth     bool
}

// NewClient validates configuration and performs no network traffic.
func NewClient(config Config) (*Client, error) {
	parsed, err := url.Parse(config.BaseURL)
	if err != nil ||
		(parsed.Scheme != "http" && parsed.Scheme != "https") ||
		parsed.Host == "" ||
		parsed.User != nil ||
		parsed.RawQuery != "" ||
		parsed.ForceQuery ||
		parsed.Fragment != "" ||
		(parsed.Path != "" && parsed.Path != "/") {
		return nil, errors.New("BaseURL must be an HTTP(S) origin")
	}
	if strings.TrimSpace(config.Username) == "" ||
		containsASCIIWhitespace(config.Username) {
		return nil, errors.New("Username must be nonblank and contain no ASCII whitespace")
	}
	if config.Password == "" || containsASCIIWhitespace(config.Password) {
		return nil, errors.New("Password must be nonblank and contain no ASCII whitespace")
	}
	if config.MaxReauth < 1 {
		return nil, errors.New("MaxReauth must be at least 1")
	}

	baseClient := config.HTTPClient
	if baseClient == nil {
		baseClient = http.DefaultClient
	}
	clientCopy := *baseClient
	clientCopy.CheckRedirect = func(
		_ *http.Request,
		_ []*http.Request,
	) error {
		return http.ErrUseLastResponse
	}
	onReauth := config.OnReauth
	if onReauth == nil {
		onReauth = func(context.Context, string, int) error { return nil }
	}

	return &Client{
		baseURL:    strings.TrimSuffix(parsed.String(), "/"),
		username:   config.Username,
		password:   config.Password,
		httpClient: &clientCopy,
		maxReauth:  config.MaxReauth,
		onReauth:   onReauth,
	}, nil
}

// Sweep logs in, lists the selected virtual machines, and brings each one to
// the desired CPU settings. A session token that expires mid-sweep is
// refreshed and the interrupted request is replayed; completed work is never
// repeated. The partial result is returned alongside any error.
func (c *Client) Sweep(
	ctx context.Context,
	request SweepRequest,
) (SweepResult, error) {
	return SweepResult{}, ErrNotImplemented
}

func validateSweepRequest(request SweepRequest) error {
	return ErrNotImplemented
}

func validateFilter(field string, values []string, allowed []string) error {
	return ErrNotImplemented
}

// planUpdate returns the UpdateSpec members that must be sent, the exact
// request body, and the members that differ but may not be modified while the
// virtual machine is not powered off.
func planUpdate(
	info CPUInfo,
	powerState string,
	desired Desired,
) ([]string, []byte, []string) {
	return nil, nil, nil
}

func (c *Client) login(ctx context.Context, state *session) error {
	return ErrNotImplemented
}

func (c *Client) listTargets(
	ctx context.Context,
	state *session,
	request SweepRequest,
) ([]VMSummary, error) {
	return nil, ErrNotImplemented
}

func (c *Client) getCPU(
	ctx context.Context,
	state *session,
	vm string,
) (CPUInfo, error) {
	return CPUInfo{}, ErrNotImplemented
}

func (c *Client) updateCPU(
	ctx context.Context,
	state *session,
	vm string,
	body []byte,
) error {
	return ErrNotImplemented
}

func cpuPath(vm string) string {
	return ""
}

// call performs a session-authenticated request, refreshing the session and
// replaying the identical request once if the token has expired.
func (c *Client) call(
	ctx context.Context,
	state *session,
	request apiRequest,
	out any,
) error {
	return ErrNotImplemented
}

// refresh runs Cis.Session_create again within the configured budget.
func (c *Client) refresh(
	ctx context.Context,
	state *session,
	operationID string,
) error {
	return ErrNotImplemented
}

// exchange performs one request without any refresh handling.
func (c *Client) exchange(
	ctx context.Context,
	request apiRequest,
	token string,
	out any,
) error {
	return ErrNotImplemented
}

func (c *Client) roundTrip(
	ctx context.Context,
	request apiRequest,
	token string,
) (int, string, []byte, error) {
	return 0, "", nil, ErrNotImplemented
}

func (c *Client) interpret(
	request apiRequest,
	status int,
	mediaType string,
	payload []byte,
	out any,
) error {
	return ErrNotImplemented
}

func newAPIError(operationID string, status int, payload []byte) *APIError {
	failure := &APIError{OperationID: operationID, Status: status}
	var wire struct {
		ErrorType string `json:"error_type"`
		Messages  []struct {
			DefaultMessage string `json:"default_message"`
		} `json:"messages"`
		Challenge string `json:"challenge"`
	}
	if json.Unmarshal(payload, &wire) == nil {
		failure.ErrorType = wire.ErrorType
		failure.Challenge = wire.Challenge
		for _, message := range wire.Messages {
			failure.Messages = append(failure.Messages, message.DefaultMessage)
		}
	}
	return failure
}

func decodeOneJSON(data []byte, out any) error {
	decoder := json.NewDecoder(bytes.NewReader(data))
	if err := decoder.Decode(out); err != nil {
		return err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return errors.New("JSON response contains trailing data")
	}
	return nil
}

func containsASCIIWhitespace(value string) bool {
	for index := 0; index < len(value); index++ {
		switch value[index] {
		case ' ', '\t', '\n', '\r', '\v', '\f':
			return true
		}
	}
	return false
}
