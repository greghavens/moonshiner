package cloneinventory

import (
	"context"
	"errors"
)

// ErrNotImplemented marks the incomplete implementation.
var ErrNotImplemented = errors.New("clone and inventory workflow is not implemented")

// NewClient validates configuration without performing network I/O.
func NewClient(Config) (*Client, error) {
	return nil, ErrNotImplemented
}

// CloneAndInventory submits one asynchronous clone, waits for terminal success,
// and returns contract-sorted VM inventory.
func (c *Client) CloneAndInventory(context.Context, CloneRequest) (CloneInventoryResult, error) {
	return CloneInventoryResult{}, ErrNotImplemented
}
