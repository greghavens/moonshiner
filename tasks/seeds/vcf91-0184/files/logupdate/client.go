package logupdate

import (
	"context"
	"errors"
)

// Client invokes the focused updateLogForwarder contract.
type Client struct{}

// NewClient validates configuration without performing network traffic.
func NewClient(cfg Config) (*Client, error) {
	return nil, errors.New("not implemented")
}

// UpdateLogForwarder replaces a log forwarder using the contract's idempotent
// PUT operation.
func (c *Client) UpdateLogForwarder(ctx context.Context, id string, update LogForwarderUpdate) (LogForwarder, error) {
	return LogForwarder{}, errors.New("not implemented")
}
