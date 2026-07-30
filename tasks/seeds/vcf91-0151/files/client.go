package vkschange

import (
	"context"
	"errors"
)

// Apply performs the ordered change documented in README.md and
// docs/contract.json.
func (c *Client) Apply(ctx context.Context, change Change) (Report, error) {
	// TODO: implement the contract.
	return newReport(), errors.New("not implemented")
}
