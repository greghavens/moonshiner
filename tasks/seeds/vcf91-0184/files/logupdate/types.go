// Package logupdate implements the focused VCF Operations Log Management
// contract recorded in docs/contract.json.
package logupdate

import (
	"encoding/json"
	"fmt"
	"net/http"
)

// Config configures a Log Management client.
type Config struct {
	BaseURL     string
	Token       string
	HTTPClient  *http.Client
	MaxAttempts int
}

// LogForwarderUpdate contains the writable LogForwarder properties in the
// exact order in which the OpenAPI schema declares them. Pointers make field
// presence explicit: nil is omitted, while a pointer to a zero value is sent.
type LogForwarderUpdate struct {
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

// LogForwarder is the updateLogForwarder response projection. ID is read-only
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

// ErrorKind classifies failures without exposing credentials or response data.
type ErrorKind string

const (
	KindHTTP      ErrorKind = "http"
	KindTransport ErrorKind = "transport"
	KindProtocol  ErrorKind = "protocol"
)

// Error is returned for updateLogForwarder failures.
type Error struct {
	OperationID string
	Kind        ErrorKind
	StatusCode  int
	Attempts    int
	cause       error
}

func (e *Error) Error() string {
	switch e.Kind {
	case KindHTTP:
		return fmt.Sprintf("%s failed with HTTP %d", e.OperationID, e.StatusCode)
	case KindTransport:
		return fmt.Sprintf("%s transport failed after %d attempt(s)", e.OperationID, e.Attempts)
	default:
		return fmt.Sprintf("%s returned an invalid response", e.OperationID)
	}
}

// Unwrap preserves context cancellation and deadline errors.
func (e *Error) Unwrap() error {
	return e.cause
}
