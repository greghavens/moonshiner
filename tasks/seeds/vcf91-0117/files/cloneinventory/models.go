// Package cloneinventory implements one focused VCF 9.1 vCenter clone workflow.
package cloneinventory

import (
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

const (
	// CloneTaskOperation submits a clone in asynchronous task mode.
	CloneTaskOperation = "Vcenter.VM_clone$Task"
	// TaskGetOperation reads one asynchronous task.
	TaskGetOperation = "Cis.Tasks_get"
	// VMListOperation lists visible virtual machines.
	VMListOperation = "Vcenter.VM_list"
)

// Config configures the focused vCenter client.
type Config struct {
	BaseURL      string
	SessionID    string
	HTTPClient   *http.Client
	Timeout      time.Duration
	PollInterval time.Duration
	MaxPolls     int
}

// CloneRequest is the required projection of Vcenter.VM.CloneSpec.
type CloneRequest struct {
	SourceVM string
	Name     string
}

// VMSummary is the focused projection of Vcenter.VM.Summary.
type VMSummary struct {
	VM            string `json:"vm"`
	Name          string `json:"name"`
	PowerState    string `json:"power_state"`
	CPUCount      *int64 `json:"cpu_count,omitempty"`
	MemorySizeMiB *int64 `json:"memory_size_mib,omitempty"`
}

// CloneInventoryResult contains terminal task evidence and stable inventory.
type CloneInventoryResult struct {
	TaskID     string
	TaskStatus string
	TaskResult json.RawMessage
	PollCount  int
	VMs        []VMSummary
}

// LocalizableMessage is the required projection of Vapi.Std.LocalizableMessage.
type LocalizableMessage struct {
	ID             string   `json:"id"`
	DefaultMessage string   `json:"default_message"`
	Args           []string `json:"args"`
}

// TaskInfo is the focused projection of Cis.Task.Info.
type TaskInfo struct {
	Description LocalizableMessage
	Service     string
	Operation   string
	Status      string
	Cancelable  bool
	Result      json.RawMessage
	ErrorData   json.RawMessage
}

// ValidationError reports a local input error.
type ValidationError struct {
	Field  string
	Reason string
}

func (e *ValidationError) Error() string {
	return fmt.Sprintf("invalid %s: %s", e.Field, e.Reason)
}

func (e *ValidationError) Format(state fmt.State, verb rune) {
	formatError(state, verb, e.Error())
}

// APIError is a well-formed non-success vAPI response.
type APIError struct {
	OperationID string
	StatusCode  int
	ErrorType   string
	Messages    []LocalizableMessage
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s failed with HTTP %d", e.OperationID, e.StatusCode)
}

func (e *APIError) Format(state fmt.State, verb rune) {
	formatError(state, verb, e.Error())
}

// ProtocolError reports a success response or error response that violates the
// focused contract.
type ProtocolError struct {
	OperationID string
	Reason      string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("%s returned an invalid response: %s", e.OperationID, e.Reason)
}

func (e *ProtocolError) Format(state fmt.State, verb rune) {
	formatError(state, verb, e.Error())
}

// TransportError reports a sanitized request transport failure.
type TransportError struct {
	OperationID string
	cause       error
}

func (e *TransportError) Error() string {
	return fmt.Sprintf("%s transport failed", e.OperationID)
}

func (e *TransportError) Format(state fmt.State, verb rune) {
	formatError(state, verb, e.Error())
}

func (e *TransportError) Unwrap() error {
	return e.cause
}

// TaskFailedError reports a terminal FAILED task while preserving structured
// task information outside the error string.
type TaskFailedError struct {
	TaskID   string
	TaskInfo TaskInfo
}

func (e *TaskFailedError) Error() string {
	return "vCenter task reached FAILED"
}

func (e *TaskFailedError) Format(state fmt.State, verb rune) {
	formatError(state, verb, e.Error())
}

// PollLimitError reports bounded exhaustion while a task remains non-terminal.
type PollLimitError struct {
	TaskID   string
	MaxPolls int
}

func (e *PollLimitError) Error() string {
	return fmt.Sprintf("vCenter task remained non-terminal after %d polls", e.MaxPolls)
}

func (e *PollLimitError) Format(state fmt.State, verb rune) {
	formatError(state, verb, e.Error())
}

func formatError(state fmt.State, verb rune, text string) {
	if verb == 'q' {
		_, _ = fmt.Fprintf(state, "%q", text)
		return
	}
	_, _ = state.Write([]byte(text))
}

// Client performs the contract-pinned clone workflow.
type Client struct {
	baseURL      string
	sessionID    string
	httpClient   *http.Client
	pollInterval time.Duration
	maxPolls     int
}
