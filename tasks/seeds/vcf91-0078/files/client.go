package nsxpolicy

import (
	"context"
	"errors"
)

// Client calls the VCF 9.1 NSX Policy API.
type Client struct{}

// NewClient validates configuration without making network calls.
func NewClient(cfg Config) (*Client, error) {
	return nil, errors.New("not implemented")
}

// ListAllSegments traverses ListAllInfraSegments and returns deterministic
// collection order.
func (c *Client) ListAllSegments(ctx context.Context, options ListOptions) ([]Segment, error) {
	return nil, errors.New("not implemented")
}
