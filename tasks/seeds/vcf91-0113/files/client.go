// Package resize applies a focused, ordered vCenter VM resize and preserves the
// outcome of every operation that was attempted.
package resize

import (
	"context"
	"errors"
	"fmt"
	"net/http"
)

const (
	CPUUpdateOperation    = "Vcenter.Vm.Hardware.Cpu_update"
	MemoryUpdateOperation = "Vcenter.Vm.Hardware.Memory_update"
	PowerStartOperation   = "Vcenter.Vm.Power_start"
)

// ErrNotImplemented is returned by the incomplete client scaffold.
var ErrNotImplemented = errors.New("vCenter resize workflow is not implemented")

// Config configures one vCenter Automation API client.
type Config struct {
	BaseURL      string
	SessionToken string
	HTTPClient   *http.Client
}

// StepResult reports one attempted operation.
type StepResult struct {
	Name        string
	OperationID string
	State       string
	HTTPStatus  int
	ErrorType   string
	Message     string
}

// ResizeReport preserves ordered outcomes, including successful earlier
// operations when a later operation fails.
type ResizeReport struct {
	VM                 string
	OverallState       string
	CompletedStepCount int
	FailedOperationID  string
	Steps              []StepResult
}

// APIError represents a well-formed non-success vAPI response.
type APIError struct {
	OperationID string
	StatusCode  int
	ErrorType   string
	Message     string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s failed with HTTP status %d", e.OperationID, e.StatusCode)
}

// ProtocolError reports response data that violates the focused contract.
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
}

func (e *TransportError) Error() string {
	return e.OperationID + " transport failure"
}

// Client applies the three-step resize-and-start workflow.
type Client struct{}

// NewClient validates configuration without performing network I/O.
func NewClient(Config) (*Client, error) {
	return nil, ErrNotImplemented
}

// ResizeAndStart changes CPU count, changes memory size, and starts the VM.
// It always returns all operation outcomes known before the first failure.
func (c *Client) ResizeAndStart(
	context.Context,
	string,
	int64,
	int64,
) (ResizeReport, error) {
	return ResizeReport{}, ErrNotImplemented
}
