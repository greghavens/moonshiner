package nsxpolicy

import (
	"context"
	"errors"
)

var errNotImplemented = errors.New("client.go is not implemented")

// Client is completed by the task solution.
type Client struct{}

// NewClient is completed by the task solution.
func NewClient(Config) (*Client, error) {
	return nil, errNotImplemented
}

// EnableSegment is completed by the task solution.
func (c *Client) EnableSegment(context.Context, string, EnableRequest) (Result, error) {
	return Result{}, errNotImplemented
}
