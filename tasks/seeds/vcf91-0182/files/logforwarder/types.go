package logforwarder

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
)

const (
	GetAllLogForwardersOperation = "getAllLogForwarders"
	CreateLogForwarderOperation  = "createLogForwarder"
)

// TokenProvider returns the current token when forceRefresh is false and a
// replacement token when forceRefresh is true.
type TokenProvider func(ctx context.Context, forceRefresh bool) (string, error)

type Config struct {
	BaseURL       string
	TokenProvider TokenProvider
	HTTPClient    *http.Client
}

// DesiredForwarder contains writable LogForwarder properties. Pointer fields
// distinguish an explicit false or zero from an unset property.
type DesiredForwarder struct {
	Certificate                *string
	ConnectionRefreshInterval  *int32
	Constraints                map[string]any
	Enabled                    *bool
	ForwardComplementaryFields *bool
	Host                       string
	Name                       string
	Port                       int32
	Protocol                   string
	SSLEnabled                 *bool
	Tags                       map[string]string
	TransportProtocol          string
	WorkerCount                *int32
}

// Forwarder is the focused response projection used by reconciliation.
type Forwarder struct {
	Certificate                *string           `json:"certificate,omitempty"`
	ConnectionRefreshInterval  *int32            `json:"connectionRefreshInterval,omitempty"`
	Constraints                json.RawMessage   `json:"constraints,omitempty"`
	Enabled                    *bool             `json:"enabled,omitempty"`
	ForwardComplementaryFields *bool             `json:"forwardComplementaryFields,omitempty"`
	Host                       string            `json:"host,omitempty"`
	ID                         string            `json:"id,omitempty"`
	Name                       string            `json:"name,omitempty"`
	Port                       int32             `json:"port,omitempty"`
	Protocol                   string            `json:"protocol,omitempty"`
	SSLEnabled                 *bool             `json:"sslEnabled,omitempty"`
	Tags                       map[string]string `json:"tags,omitempty"`
	TransportProtocol          string            `json:"transportProtocol,omitempty"`
	WorkerCount                *int32            `json:"workerCount,omitempty"`
}

type APIError struct {
	OperationID string
	StatusCode  int
	cause       error
}

func (e *APIError) Error() string {
	if e.StatusCode == 0 {
		return fmt.Sprintf("%s request failed", e.OperationID)
	}
	return fmt.Sprintf("%s returned HTTP %d", e.OperationID, e.StatusCode)
}

func (e *APIError) Unwrap() error { return e.cause }

type ProtocolError struct {
	OperationID string
	Problem     string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("%s response violated the contract: %s", e.OperationID, e.Problem)
}

type TokenProviderError struct {
	cause error
}

func (e *TokenProviderError) Error() string { return "token provider failed" }
func (e *TokenProviderError) Unwrap() error { return e.cause }
