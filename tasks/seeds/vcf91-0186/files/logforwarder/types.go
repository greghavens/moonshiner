// Package logforwarder implements the focused VCF Operations Log Management
// contract recorded in docs/contract.json.
package logforwarder

import (
	"encoding/json"
	"fmt"
	"net/http"
)

const (
	OperationTestLogForwarderConnection = "testLogForwarderConnection"
	OperationCreateLogForwarder         = "createLogForwarder"
)

// Config configures a Log Management client.
type Config struct {
	BaseURL    string
	Token      string
	HTTPClient *http.Client
}

// LogForwarderInput contains the writable LogForwarder properties in the exact
// order in which the OpenAPI schema declares them. Pointers make presence
// explicit: nil is omitted, while a pointer to a zero value is sent.
type LogForwarderInput struct {
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

// ErrorKind classifies failures without exposing credentials or response data.
type ErrorKind string

const (
	KindHTTP      ErrorKind = "http"
	KindTransport ErrorKind = "transport"
	KindProtocol  ErrorKind = "protocol"
)

// OperationError reports which contract operation failed.
type OperationError struct {
	OperationID string
	Kind        ErrorKind
	StatusCode  int
	cause       error
}

func (e *OperationError) Error() string {
	switch e.Kind {
	case KindHTTP:
		return fmt.Sprintf("%s failed with HTTP %d", e.OperationID, e.StatusCode)
	case KindTransport:
		return fmt.Sprintf("%s transport failed", e.OperationID)
	default:
		return fmt.Sprintf("%s returned an invalid response", e.OperationID)
	}
}

// Unwrap keeps context cancellation and deadline errors discoverable.
func (e *OperationError) Unwrap() error {
	return e.cause
}
