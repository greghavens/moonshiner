package supervisorvks

import (
	"context"
	"net/http"
)

// NewClient constructs a client without performing I/O.
func NewClient(vcenterOrigin, kubeOrigin, sessionID string, tokens TokenSource, httpClient *http.Client) (*Client, error) {
	return nil, ErrNotImplemented
}

// Ensure creates missing resources and reports which resources it created.
func (c *Client) Ensure(ctx context.Context, namespace NamespaceSpec, cluster ClusterSpec) (Result, error) {
	return Result{}, ErrNotImplemented
}
