// Package logtask polls structured VCF Operations Log Management events for
// the terminal state of an already-started asynchronous VCF operation.
package logtask

import (
	"fmt"
	"net/http"
	"time"
)

const (
	// SearchOperationID is the exact OpenAPI operationId used for every poll.
	SearchOperationID = "executeLogSearchQuery_1"

	// StateEventType is the structured event_type emitted by the upstream
	// controller for asynchronous operation progress.
	StateEventType = "VCF_ASYNC_OPERATION_STATE"
)

// OperationState is a structured operation_state field value.
type OperationState string

const (
	StateQueued    OperationState = "QUEUED"
	StateRunning   OperationState = "RUNNING"
	StateBlocked   OperationState = "BLOCKED"
	StateSucceeded OperationState = "SUCCEEDED"
	StateFailed    OperationState = "FAILED"
	StateCancelled OperationState = "CANCELLED"
)

// Config configures a Log Management client.
type Config struct {
	BaseURL      string
	Token        string
	HTTPClient   *http.Client
	PollInterval time.Duration
	MaxPolls     int
}

// WaitRequest identifies the asynchronous operation and its search window.
// EndTimeMS is optional. A nil value is omitted from the range query.
type WaitRequest struct {
	OperationID string
	StartTimeMS int64
	EndTimeMS   *int64
}

// Result describes the terminal state event observed by the client.
type Result struct {
	OperationID string
	State       OperationState
	Polls       int
	ObservedAt  int64
	Message     string
}

// APIError reports an HTTP or transport failure without retaining credentials
// or raw response data.
type APIError struct {
	Operation  string
	StatusCode int
}

func (e *APIError) Error() string {
	if e.StatusCode == 0 {
		return fmt.Sprintf("%s transport failure", e.Operation)
	}
	return fmt.Sprintf("%s returned HTTP %d", e.Operation, e.StatusCode)
}

// ProtocolError reports malformed or contradictory successful response data.
type ProtocolError struct {
	Operation string
	Problem   string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("%s protocol error: %s", e.Operation, e.Problem)
}

// OperationFailedError reports a server-authored terminal failure state.
type OperationFailedError struct {
	OperationID string
	State       OperationState
	Polls       int
}

func (e *OperationFailedError) Error() string {
	return fmt.Sprintf("operation %q reached terminal state %s after %d polls", e.OperationID, e.State, e.Polls)
}

// PollTimeoutError reports that no terminal event was observed within MaxPolls.
type PollTimeoutError struct {
	OperationID string
	Polls       int
}

func (e *PollTimeoutError) Error() string {
	return fmt.Sprintf("operation %q did not reach a terminal state after %d polls", e.OperationID, e.Polls)
}
