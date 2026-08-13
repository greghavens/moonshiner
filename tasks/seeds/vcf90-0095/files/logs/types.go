// Package logs implements the VCF Operations for Logs 9.0 REST operations
// selected in docs/contract.json.
package logs

import (
	"fmt"
	"net/http"
)

// Client calls a VCF Operations for Logs appliance.
type Client struct {
	baseURL    string
	token      string
	httpClient *http.Client
}

// Forwarder is the forwarders.get.response schema.
type Forwarder struct {
	Name                       string            `json:"name"`
	Host                       string            `json:"host"`
	Port                       int               `json:"port"`
	Protocol                   string            `json:"protocol"`
	SSLEnabled                 bool              `json:"sslEnabled"`
	WorkerCount                int               `json:"workerCount"`
	ConnectionRefreshInterval  int               `json:"connectionRefreshInterval,omitempty"`
	DiskCacheSize              int64             `json:"diskCacheSize"`
	Tags                       map[string]string `json:"tags"`
	Filter                     string            `json:"filter"`
	TransportProtocol          string            `json:"transportProtocol,omitempty"`
	ForwardComplementaryFields bool              `json:"forwardComplementaryFields"`
	ID                         string            `json:"id"`
	ForwarderStats             *ForwarderStats   `json:"forwarderStats,omitempty"`
}

// ForwarderStats is the optional extended information returned when requested.
type ForwarderStats struct {
	State         string  `json:"state"`
	Rate          float64 `json:"rate"`
	LogsForwarded int64   `json:"logsForwarded"`
	LogsDropped   int64   `json:"logsDropped"`
}

// UpdateForwarderRequest is the forwarders.put.request schema. Pointer fields
// distinguish an omitted optional value from an explicitly supplied zero value.
type UpdateForwarderRequest struct {
	Host                       string             `json:"host"`
	Port                       int                `json:"port"`
	Protocol                   string             `json:"protocol"`
	SSLEnabled                 bool               `json:"sslEnabled"`
	AcceptCert                 *bool              `json:"acceptCert,omitempty"`
	Name                       *string            `json:"name,omitempty"`
	WorkerCount                *int               `json:"workerCount,omitempty"`
	DiskCacheSize              *int64             `json:"diskCacheSize,omitempty"`
	Tags                       *map[string]string `json:"tags,omitempty"`
	Filter                     *string            `json:"filter,omitempty"`
	TransportProtocol          *string            `json:"transportProtocol,omitempty"`
	ForwardComplementaryFields *bool              `json:"forwardComplementaryFields,omitempty"`
	TestConnection             *bool              `json:"testConnection,omitempty"`
}

// ForwarderChange is one ordered update in an ApplyForwarderUpdates call.
type ForwarderChange struct {
	ID      string
	Request UpdateForwarderRequest
}

// StepResult records the outcome of one attempted change.
type StepResult struct {
	ID        string
	Forwarder *Forwarder
	Err       error
}

// APIError is returned for a non-successful HTTP response.
type APIError struct {
	StatusCode   int            `json:"-"`
	ErrorMessage string         `json:"errorMessage"`
	ErrorCode    string         `json:"errorCode,omitempty"`
	ErrorDetails map[string]any `json:"errorDetails,omitempty"`
}

func (e *APIError) Error() string {
	if e.ErrorCode != "" {
		return fmt.Sprintf("vcf operations for logs: HTTP %d: %s (%s)", e.StatusCode, e.ErrorMessage, e.ErrorCode)
	}
	return fmt.Sprintf("vcf operations for logs: HTTP %d: %s", e.StatusCode, e.ErrorMessage)
}
