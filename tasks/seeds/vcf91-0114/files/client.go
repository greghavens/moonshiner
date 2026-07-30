// Package cpuguard applies one vCenter VM CPU-count change only after a
// power-state precheck proves that the VM is powered off.
package cpuguard

import (
	"context"
	"errors"
	"fmt"
	"net/http"
)

const (
	PowerGetOperation  = "Vcenter.Vm.Power_get"
	CPUUpdateOperation = "Vcenter.Vm.Hardware.Cpu_update"
)

// ErrNotImplemented is returned by the incomplete client scaffold.
var ErrNotImplemented = errors.New("guarded vCenter CPU update is not implemented")

// Config configures one focused vCenter Automation API client.
type Config struct {
	BaseURL      string
	SessionToken string
	HTTPClient   *http.Client
}

// LocalizableMessage is the focused projection of a vAPI error message.
type LocalizableMessage struct {
	ID             string
	DefaultMessage string
}

// CPUUpdateResult records the completed guarded workflow.
type CPUUpdateResult struct {
	VM                    string
	PreviousPowerState    string
	CPUCount              int64
	CompletedOperationIDs []string
}

// ValidationError reports invalid local input.
type ValidationError struct {
	Field  string
	Reason string
}

func (e *ValidationError) Error() string {
	return e.Field + " is invalid"
}

// PrecheckError reports a valid power state that blocks the mutation.
type PrecheckError struct {
	VM            string
	ObservedState string
}

func (e *PrecheckError) Error() string {
	return "vCenter CPU update blocked by power-state precheck"
}

// APIError represents a well-formed non-success vAPI response.
type APIError struct {
	OperationID string
	StatusCode  int
	ErrorType   string
	Messages    []LocalizableMessage
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s failed with HTTP status %d", e.OperationID, e.StatusCode)
}

// ProtocolError reports a response that violates the focused contract.
type ProtocolError struct {
	OperationID string
	Reason      string
}

func (e *ProtocolError) Error() string {
	return e.OperationID + " response violated the focused vCenter contract"
}

// TransportError reports a transport failure without exposing its cause.
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

// Client performs the two-operation guarded update.
type Client struct{}

// NewClient validates configuration without performing network I/O.
func NewClient(Config) (*Client, error) {
	return nil, ErrNotImplemented
}

// SetCPUCountIfPoweredOff reads power state and updates count only when the
// observed state is exactly POWERED_OFF.
func (c *Client) SetCPUCountIfPoweredOff(
	context.Context,
	string,
	int64,
) (CPUUpdateResult, error) {
	return CPUUpdateResult{}, ErrNotImplemented
}
