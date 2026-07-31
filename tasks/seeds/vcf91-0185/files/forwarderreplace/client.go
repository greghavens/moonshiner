package forwarderreplace

import (
	"context"
	"errors"
)

// Client executes the focused replacement workflow.
type Client struct{}

// NewClient is intentionally incomplete.
func NewClient(Config) (*Client, error) {
	return &Client{}, nil
}

// ReplaceLogForwarder is intentionally incomplete.
func (c *Client) ReplaceLogForwarder(context.Context, string, LogForwarderCreate) (ReplaceResult, error) {
	return ReplaceResult{}, errors.New("not implemented")
}
