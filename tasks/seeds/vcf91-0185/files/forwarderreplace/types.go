// Package forwarderreplace implements the focused VCF Operations Log
// Management contract recorded in docs/contract.json.
package forwarderreplace

import (
	"encoding/json"
	"fmt"
	"net/http"
)

// Config configures a Log Management client.
type Config struct {
	BaseURL    string
	Token      string
	HTTPClient *http.Client
}

// LogForwarderCreate contains writable LogForwarder properties in the exact
// order in which the OpenAPI schema declares them. Nil means omitted; a
// pointer to a zero value means explicitly present.
type LogForwarderCreate struct {
	Certificate                *string            `json:"certificate,omitempty"`
	ConnectionRefreshInterval  *int32             `json:"connectionRefreshInterval,omitempty"`
	Constraints                *json.RawMessage   `json:"constraints,omitempty"`
	Enabled                    *bool              `json:"enabled,omitempty"`
	ForwardComplementaryFields *bool              `json:"forwardComplementaryFields,omitempty"`
	Host                       *string            `json:"host,omitempty"`
	Name                       *string            `json:"name,omitempty"`
	Port                       *int32             `json:"port,omitempty"`
	Protocol                   *string            `json:"protocol,omitempty"`
	SSLEnabled                 *bool              `json:"sslEnabled,omitempty"`
	Tags                       *map[string]string `json:"tags,omitempty"`
	TransportProtocol          *string            `json:"transportProtocol,omitempty"`
	WorkerCount                *int32             `json:"workerCount,omitempty"`
}

// LogForwarder is the createLogForwarder response projection. ID is read-only
// in the specification and therefore exists only on the response model.
type LogForwarder struct {
	Certificate                *string            `json:"certificate,omitempty"`
	ConnectionRefreshInterval  *int32             `json:"connectionRefreshInterval,omitempty"`
	Constraints                *json.RawMessage   `json:"constraints,omitempty"`
	Enabled                    *bool              `json:"enabled,omitempty"`
	ForwardComplementaryFields *bool              `json:"forwardComplementaryFields,omitempty"`
	Host                       *string            `json:"host,omitempty"`
	ID                         string             `json:"id,omitempty"`
	Name                       *string            `json:"name,omitempty"`
	Port                       *int32             `json:"port,omitempty"`
	Protocol                   *string            `json:"protocol,omitempty"`
	SSLEnabled                 *bool              `json:"sslEnabled,omitempty"`
	Tags                       *map[string]string `json:"tags,omitempty"`
	TransportProtocol          *string            `json:"transportProtocol,omitempty"`
	WorkerCount                *int32             `json:"workerCount,omitempty"`
}

// Step identifies one operation in the ordered replacement.
type Step string

const (
	StepCreate Step = "create"
	StepDelete Step = "delete"
)

// StepOutcome reports exactly what happened for one operation.
type StepOutcome struct {
	OperationID string
	Attempted   bool
	Succeeded   bool
	StatusCode  int
}

// ReplaceResult remains meaningful when either operation fails.
type ReplaceResult struct {
	Created *LogForwarder
	Create  StepOutcome
	Delete  StepOutcome
}

// ErrorKind classifies a failing step without exposing sensitive data.
type ErrorKind string

const (
	KindHTTP      ErrorKind = "http"
	KindTransport ErrorKind = "transport"
	KindProtocol  ErrorKind = "protocol"
)

// Error describes the exact operation and step that failed.
type Error struct {
	OperationID string
	Step        Step
	Kind        ErrorKind
	StatusCode  int
	cause       error
}

func (e *Error) Error() string {
	switch e.Kind {
	case KindHTTP:
		return fmt.Sprintf("%s (%s step) failed with HTTP %d", e.OperationID, e.Step, e.StatusCode)
	case KindTransport:
		return fmt.Sprintf("%s (%s step) transport failed", e.OperationID, e.Step)
	default:
		return fmt.Sprintf("%s (%s step) returned an invalid response", e.OperationID, e.Step)
	}
}

// Unwrap preserves context cancellation and deadline errors.
func (e *Error) Unwrap() error {
	return e.cause
}
