// Package vcfops implements the small VCF Operations 9.0 REST subset recorded
// in docs/contract.json.
package vcfops

import (
	"context"
	"errors"
	"net/http"
)

// ErrNotImplemented is returned by the starter implementation.
var ErrNotImplemented = errors.New("vcfops: client not implemented")

// Credentials is the request body for acquireToken. AuthSource is optional.
type Credentials struct {
	Username   string
	Password   string
	AuthSource *string
}

// AlertQuery contains the optional filters supported by this package and the
// requested page size. PageSize must be positive.
type AlertQuery struct {
	IDs         []string
	ResourceIDs []string
	PageSize    int
}

// Alert is the portion of the VCF Operations alert representation used by this
// integration.
type Alert struct {
	AlertID       string `json:"alertId"`
	ResourceID    string `json:"resourceId"`
	AlertLevel    string `json:"alertLevel"`
	StartTimeUTC  int64  `json:"startTimeUTC"`
	UpdateTimeUTC int64  `json:"updateTimeUTC"`
}

// Client calls a VCF Operations endpoint.
type Client struct {
	baseURL     string
	credentials Credentials
	httpClient  *http.Client
}

// NewClient constructs a client. baseURL is the server origin; the /suite-api
// prefix from the contract is added by the client.
func NewClient(baseURL string, credentials Credentials, httpClient *http.Client) (*Client, error) {
	return nil, ErrNotImplemented
}

// CollectAlerts returns every matching alert, in server page order.
func (c *Client) CollectAlerts(ctx context.Context, query AlertQuery) ([]Alert, error) {
	return nil, ErrNotImplemented
}
