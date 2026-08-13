package credrotate

import (
	"context"
	"errors"
)

// ErrNotImplemented marks the implementation point for this exercise.
var ErrNotImplemented = errors.New("credrotate client is not implemented")

// Client executes the focused VCF Automation credential-rotation workflow and
// must be safe for concurrent use by multiple goroutines.
type Client struct{}

// NewClient validates the configuration and constructs a client. It performs
// no I/O: the bearer token is acquired lazily on first use.
func NewClient(Config) (*Client, error) {
	return nil, ErrNotImplemented
}

// RotateCredentials replaces the cloud account's stored privateKeyId and
// privateKey, waits for the asynchronous request to reach a terminal state,
// and reads the account back.
func (c *Client) RotateCredentials(context.Context, string, UpdateCloudAccountInput) (RotationResult, error) {
	return RotationResult{}, ErrNotImplemented
}

// GetCloudAccount reads one cloud account.
func (c *Client) GetCloudAccount(context.Context, string) (CloudAccount, error) {
	return CloudAccount{}, ErrNotImplemented
}
