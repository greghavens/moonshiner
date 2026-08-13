// Package opsadapter registers adapter instances against the VMware Cloud
// Foundation Operations API.
//
// Registration is gated: the connection precheck runs first and the adapter
// instance is only created when the precheck passes.
package opsadapter

import (
	"context"
	"errors"
	"fmt"
	"net/http"
)

var errNotImplemented = errors.New("opsadapter: not implemented")

// PrecheckError reports that the connection precheck rejected the adapter
// instance. When Register returns this error no adapter instance was created.
type PrecheckError struct {
	// StatusCode is the status the precheck answered with.
	StatusCode int
	// Message is the message reported by the server.
	Message string
}

func (e *PrecheckError) Error() string {
	return fmt.Sprintf("opsadapter: connection precheck failed with status %d: %s", e.StatusCode, e.Message)
}

// APIError reports a request that failed for a reason other than the precheck
// rejecting the adapter instance.
type APIError struct {
	// OperationID is the operationId of the failed call.
	OperationID string
	// StatusCode is the status the server answered with.
	StatusCode int
	// Message is the message reported by the server.
	Message string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("opsadapter: %s failed with status %d: %s", e.OperationID, e.StatusCode, e.Message)
}

// Client talks to one VCF Operations deployment.
type Client struct {
	baseURL string
	http    *http.Client
}

// NewClient returns a client for the deployment at baseURL, which is the
// scheme and authority of the deployment without the API base path, for
// example "https://ops.example.com". A trailing slash is accepted.
//
// When httpClient is nil a default client is used. NewClient reports an error
// when baseURL is empty or cannot be parsed as an absolute HTTP URL.
func NewClient(baseURL string, httpClient *http.Client) (*Client, error) {
	return nil, errNotImplemented
}

// AcquireToken exchanges credentials for a session token.
//
// authSource is optional; the empty string means the deployment's local user
// directory and must be left out of the request entirely.
func (c *Client) AcquireToken(ctx context.Context, username, password, authSource string) (string, error) {
	return "", errNotImplemented
}

// Register runs the connection precheck for spec and, only if the precheck
// passes, creates the adapter instance.
//
// When the precheck rejects the adapter instance Register returns a
// *PrecheckError and makes no further calls, so the deployment is left
// unchanged. When any other call fails Register returns an *APIError.
func (c *Client) Register(ctx context.Context, token string, spec CreateAdapterInstance) (AdapterInstance, error) {
	return AdapterInstance{}, errNotImplemented
}
