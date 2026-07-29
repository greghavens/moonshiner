package nsxpolicy

import (
	"context"
)

// NewClient validates cfg and returns an independent client.
func NewClient(cfg Config) (*Client, error) {
	return nil, &ValidationError{Field: "configuration: TODO"}
}

// ApplyIPBlock creates or updates one resource-addressed IP block.
func (c *Client) ApplyIPBlock(
	ctx context.Context,
	ipBlockID string,
	block IPAddressBlock,
) (Result, error) {
	return Result{}, &ValidationError{Field: "request: TODO"}
}
