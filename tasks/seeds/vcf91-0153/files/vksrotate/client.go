package vksrotate

import (
	"context"
	"errors"
)

var errNotImplemented = errors.New("vksrotate: not implemented")

// NewClient validates config and constructs a client without performing I/O.
func NewClient(config Config) (*Client, error) {
	return nil, errNotImplemented
}

// Rotate atomically replaces both credentials without performing I/O.
func (c *Client) Rotate(credentials Credentials) error {
	return errNotImplemented
}

// Inspect reads the Supervisor namespace and then its VKS Cluster collection.
func (c *Client) Inspect(ctx context.Context, namespace string) (Snapshot, error) {
	return Snapshot{}, errNotImplemented
}
