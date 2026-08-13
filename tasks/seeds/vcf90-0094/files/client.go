package vcflogs

import (
	"context"
	"errors"
	"net/http"
)

// ForwarderUpdate is the request body for PUT_log-forwarder-id.
// Pointer fields distinguish an omitted optional value from an explicitly set
// zero value.
type ForwarderUpdate struct {
	AcceptCert                 *bool             `json:"acceptCert,omitempty"`
	Name                       *string           `json:"name,omitempty"`
	Host                       string            `json:"host"`
	Port                       int               `json:"port"`
	Protocol                   string            `json:"protocol"`
	SSLEnabled                 bool              `json:"sslEnabled"`
	WorkerCount                *int              `json:"workerCount,omitempty"`
	DiskCacheSize              *int              `json:"diskCacheSize,omitempty"`
	Tags                       map[string]string `json:"tags,omitempty"`
	Filter                     *string           `json:"filter,omitempty"`
	TransportProtocol          *string           `json:"transportProtocol,omitempty"`
	ForwardComplementaryFields *bool             `json:"forwardComplementaryFields,omitempty"`
	TestConnection             *bool             `json:"testConnection,omitempty"`
}

// Forwarder is the successful response for PUT_log-forwarder-id.
type Forwarder struct {
	Name                       string            `json:"name"`
	Host                       string            `json:"host"`
	Port                       int               `json:"port"`
	Protocol                   string            `json:"protocol"`
	SSLEnabled                 bool              `json:"sslEnabled"`
	WorkerCount                int               `json:"workerCount"`
	ConnectionRefreshInterval  int               `json:"connectionRefreshInterval,omitempty"`
	DiskCacheSize              int               `json:"diskCacheSize"`
	Tags                       map[string]string `json:"tags"`
	Filter                     string            `json:"filter"`
	TransportProtocol          string            `json:"transportProtocol,omitempty"`
	ForwardComplementaryFields bool              `json:"forwardComplementaryFields"`
	ID                         string            `json:"id"`
}

// Client calls the VCF Operations for Logs REST API.
type Client struct {
	baseURL     string
	bearerToken string
	httpClient  *http.Client
}

func NewClient(baseURL, bearerToken string, httpClient *http.Client) *Client {
	return &Client{baseURL: baseURL, bearerToken: bearerToken, httpClient: httpClient}
}

// UpdateForwarder updates a log-forwarding destination.
func (c *Client) UpdateForwarder(ctx context.Context, id string, update ForwarderUpdate) (Forwarder, error) {
	return Forwarder{}, errors.New("UpdateForwarder is not implemented")
}
