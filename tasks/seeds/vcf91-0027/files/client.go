package sddcmanager

import (
	"context"
	"errors"
	"net/http"
)

// ErrNotImplemented is returned by the initial scaffold.
var ErrNotImplemented = errors.New("sddc manager client is not implemented")

// Client calls the VCF 9.1 SDDC Manager REST API.
type Client struct{}

// ListHostsOptions contains the non-deprecated query options for getHosts.
// Empty strings and a zero PageSize are unset. Boolean pointers distinguish
// an unset value from an explicitly supplied false value.
type ListHostsOptions struct {
	PageSize                   int
	FQDN                       string
	Status                     string
	DomainID                   string
	ClusterID                  string
	NetworkPoolID              string
	StorageType                string
	DatastoreName              string
	IPAddressVersionForVmotion string
	IsStandalone               *bool
	IsLifecycleManaged         *bool
	IsVsanWitnessHost          *bool
}

// Host is the stable, consumed projection of an SDDC Manager host.
type Host struct {
	ID     string `json:"id"`
	FQDN   string `json:"fqdn"`
	Status string `json:"status"`
}

// NewClient creates an SDDC Manager client rooted at baseURL.
func NewClient(baseURL string, httpClient *http.Client) (*Client, error) {
	return nil, ErrNotImplemented
}

// ListAllHosts retrieves every getHosts page and returns a stable ordering.
func (c *Client) ListAllHosts(ctx context.Context, options ListHostsOptions) ([]Host, error) {
	return nil, ErrNotImplemented
}
