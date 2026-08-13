// Package vcfopslogs implements a focused VCF Operations Log Management client.
package vcfopslogs

import (
	"context"
	"errors"
	"net/http"
)

var ErrNotImplemented = errors.New("vcfopslogs: not implemented")

// Client is a client for the focused Log Management agent-secret contract.
type Client struct{}

// CreateAgentSecretRequest is the request for createAgentSecret. Name is
// optional in the pinned OpenAPI schema; nil must be omitted from JSON.
type CreateAgentSecretRequest struct {
	Name *string `json:"name,omitempty"`
}

// AgentSecret is returned by createAgentSecret.
type AgentSecret struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	Secret string `json:"secret"`
	Status string `json:"status"`
}

// CreateAgentSessionOptions controls createAgentSession. TTL is milliseconds.
// Nil means the field is not sent; a pointer to zero sends an explicit zero.
type CreateAgentSessionOptions struct {
	TTL *int64 `json:"ttl,omitempty"`
}

// AgentSession is returned by createAgentSession.
type AgentSession struct {
	AccessToken string `json:"access_token"`
	Name        string `json:"name"`
	NewSecret   string `json:"new_secret"`
	TTL         int64  `json:"ttl"`
}

// APIError represents a non-2xx Log Management response.
type APIError struct {
	StatusCode   int
	ErrorCode    string `json:"errorCode"`
	ErrorDetails any    `json:"errorDetails"`
	ErrorMessage string `json:"errorMessage"`
}

func (e *APIError) Error() string {
	return "vcfopslogs: API request failed"
}

// NewClient constructs a client. A nil httpClient uses http.DefaultClient.
func NewClient(baseURL, adminToken, initialAgentSecret string, httpClient *http.Client) (*Client, error) {
	return nil, ErrNotImplemented
}

// CreateAgentSecret calls operationId createAgentSecret.
func (c *Client) CreateAgentSecret(ctx context.Context, request CreateAgentSecretRequest) (AgentSecret, error) {
	return AgentSecret{}, ErrNotImplemented
}

// CreateAgentSession calls operationId createAgentSession and commits the
// returned new_secret before allowing the next exchange to start.
func (c *Client) CreateAgentSession(ctx context.Context, options CreateAgentSessionOptions) (AgentSession, error) {
	return AgentSession{}, ErrNotImplemented
}
