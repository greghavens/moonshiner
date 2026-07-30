package sessionrotation

import (
	"context"
)

// NewClient validates cfg and creates the initial vCenter API session.
func NewClient(context.Context, Config) (*Client, error) {
	return nil, ErrNotImplemented
}

// ListVMs lists virtual machines using the session generation captured when
// this call begins.
func (c *Client) ListVMs(
	context.Context,
	ListOptions,
) ([]VMSummary, error) {
	return nil, ErrNotImplemented
}

// RotatePassword creates and publishes a replacement session, drains requests
// pinned to the old generation, and then retires the old session.
func (c *Client) RotatePassword(context.Context, string) error {
	return ErrNotImplemented
}

// Close prevents new work, drains the active generation, and retires it.
func (c *Client) Close(context.Context) error {
	return ErrNotImplemented
}
