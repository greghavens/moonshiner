package logforwarder

import (
	"context"
	"errors"
)

// Client is implemented by the exercise.
type Client struct{}

func NewClient(Config) (*Client, error) {
	return nil, errors.New("not implemented")
}

func (c *Client) Reconcile(context.Context, []DesiredForwarder) ([]Forwarder, error) {
	return nil, errors.New("not implemented")
}
