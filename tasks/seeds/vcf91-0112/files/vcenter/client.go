package vcenter

import (
	"context"
)

// NewClient validates cfg and returns an independent client.
func NewClient(cfg Config) (*Client, error) {
	return nil, &ValidationError{Field: "configuration: TODO"}
}

// CreateLocalLibrary creates one local Content Library with retry-safe semantics.
func (c *Client) CreateLocalLibrary(
	ctx context.Context,
	clientToken string,
	spec LocalLibrarySpec,
) (CreateResult, error) {
	return CreateResult{}, &ValidationError{Field: "request: TODO"}
}
