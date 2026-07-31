package logforwarder

import (
	"context"
	"errors"
)

// ErrNotImplemented marks the implementation point for this exercise.
var ErrNotImplemented = errors.New("logforwarder client is not implemented")

// Client executes the focused Log Management workflow.
type Client struct{}

// NewClient validates and constructs a client.
func NewClient(Config) (*Client, error) {
	return nil, ErrNotImplemented
}

// PrecheckAndCreate tests the proposed forwarder connection and creates the
// forwarder only when that precheck succeeds.
func (c *Client) PrecheckAndCreate(context.Context, LogForwarderInput) (LogForwarder, error) {
	return LogForwarder{}, ErrNotImplemented
}
