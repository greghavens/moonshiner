package vcfops

import (
	"context"
	"errors"
	"net/http"
)

// CollectorGroupInput is the desired, user-settable portion of a collector
// group. Pointer booleans distinguish an explicit false from an unset field.
type CollectorGroupInput struct {
	Name                  string  `json:"name"`
	Description           *string `json:"description,omitempty"`
	CollectorIDs          []int32 `json:"collectorId,omitempty"`
	HAEnabled             *bool   `json:"haEnabled,omitempty"`
	LBEnabled             *bool   `json:"lbEnabled,omitempty"`
	VirtualIP             *string `json:"virtualIP,omitempty"`
	CheckCollectorMembers *bool   `json:"-"`
}

// CollectorGroup is the collector-group representation returned by VCF
// Operations for the operations used by this package.
type CollectorGroup struct {
	ID            string  `json:"id"`
	Name          string  `json:"name"`
	Description   *string `json:"description,omitempty"`
	CollectorIDs  []int32 `json:"collectorId,omitempty"`
	SystemDefined *bool   `json:"systemDefined,omitempty"`
	HAEnabled     *bool   `json:"haEnabled,omitempty"`
	LBEnabled     *bool   `json:"lbEnabled,omitempty"`
	VirtualIP     *string `json:"virtualIP,omitempty"`
}

// ErrNotImplemented marks the starter implementation.
var ErrNotImplemented = errors.New("vcfops: not implemented")

// Client calls VCF Operations.
type Client struct{}

// NewClient constructs a client. baseURL is the appliance origin; API paths
// are rooted beneath the /suite-api server path from the official contract.
func NewClient(baseURL, token string, httpClient *http.Client) (*Client, error) {
	return &Client{}, nil
}

// EnsureCollectorGroup returns the group with desired.Name, creating it only
// when it is absent. created reports whether this invocation created it.
func (c *Client) EnsureCollectorGroup(ctx context.Context, desired CollectorGroupInput) (group CollectorGroup, created bool, err error) {
	return CollectorGroup{}, false, ErrNotImplemented
}
