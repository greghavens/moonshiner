package logs

import (
	"context"
	"errors"
	"net/http"
)

var errNotImplemented = errors.New("VCF Operations for Logs client is not implemented")

// NewClient constructs a client for an appliance origin.
func NewClient(baseURL, sessionToken string, httpClient *http.Client) (*Client, error) {
	return nil, errNotImplemented
}

// ListForwarders implements GET_log-forwarder.
func (c *Client) ListForwarders(ctx context.Context, showDetails *bool) ([]Forwarder, error) {
	return nil, errNotImplemented
}

// UpdateForwarder implements PUT_log-forwarder-id.
func (c *Client) UpdateForwarder(ctx context.Context, id string, request UpdateForwarderRequest) (Forwarder, error) {
	return Forwarder{}, errNotImplemented
}

// ApplyForwarderUpdates applies changes in order until one fails.
func (c *Client) ApplyForwarderUpdates(ctx context.Context, changes []ForwarderChange) ([]StepResult, error) {
	return nil, errNotImplemented
}
