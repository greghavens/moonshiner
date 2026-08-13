package vcfnetworks

import (
	"context"
	"errors"
	"net/http"
)

// PasswordCredentials mirrors the specification PasswordCredentials object
// carried by a VCenterDataSourceRequest.
type PasswordCredentials struct {
	Username string `json:"username"`
	Password string `json:"password,omitempty"`
}

// VcenterDataSource is the focused VCenterDataSource representation returned by
// getVcenter and addVcenterDatasource.
type VcenterDataSource struct {
	EntityID   string `json:"entity_id"`
	EntityType string `json:"entity_type"`
	IP         string `json:"ip"`
	FQDN       string `json:"fqdn"`
	ProxyID    string `json:"proxy_id"`
	Nickname   string `json:"nickname"`
	Enabled    bool   `json:"enabled"`
	Notes      string `json:"notes"`
}

// VcenterSpec describes the desired vCenter data source. Nickname, ProxyID and
// Username are required by the specification. Exactly one of IP or FQDN
// identifies the target. Enabled and Notes are optional: a nil pointer means the
// member is omitted from the request body entirely.
type VcenterSpec struct {
	Nickname string
	ProxyID  string
	IP       string
	FQDN     string
	Username string
	Password string
	Enabled  *bool
	Notes    *string
}

// EnsureResult reports the data source for the requested target and whether this
// call is the one that created it.
type EnsureResult struct {
	DataSource VcenterDataSource
	Created    bool
}

// APIError is a decoded non-success response body.
type APIError struct {
	StatusCode int
	Code       int
	Message    string
}

func (e *APIError) Error() string { return "VCF Operations for Networks API request failed" }

// ProtocolError reports a malformed or contract-violating response.
type ProtocolError struct{ Reason string }

func (e *ProtocolError) Error() string {
	return "VCF Operations for Networks protocol error: " + e.Reason
}

// Client calls the VCF Operations for Networks API. It is safe for concurrent
// use by multiple goroutines.
type Client struct{}

// NewClient constructs a client for the service root baseURL. It is
// intentionally incomplete.
func NewClient(baseURL, username, password string, httpClient *http.Client) (*Client, error) {
	return nil, errors.New("TODO: implement NewClient")
}

// EnsureVcenterDataSource registers the vCenter data source described by spec if
// and only if no data source for the same target already exists.
func (c *Client) EnsureVcenterDataSource(ctx context.Context, spec VcenterSpec) (EnsureResult, error) {
	return EnsureResult{}, errors.New("TODO: implement EnsureVcenterDataSource")
}
