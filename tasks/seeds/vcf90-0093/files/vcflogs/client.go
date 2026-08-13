// Package vcflogs is a small client for the VCF Operations for Logs v2 API.
package vcflogs

import (
	"context"
	"errors"
	"net/http"
)

// Field is a field attached to a log event.
type Field struct {
	Name          string `json:"name,omitempty"`
	Content       string `json:"content,omitempty"`
	StartPosition *int   `json:"startPosition,omitempty"`
	Length        *int   `json:"length,omitempty"`
}

// Event is a VCF Operations for Logs event.
type Event struct {
	Text            string  `json:"text,omitempty"`
	Timestamp       int64   `json:"timestamp,omitempty"`
	TimestampString string  `json:"timestampString,omitempty"`
	Fields          []Field `json:"fields,omitempty"`
}

// Client calls a VCF Operations for Logs endpoint.
type Client struct {
	baseURL   string
	sessionID string
	http      *http.Client
}

// NewClient constructs a client with the caller-provided transport.
func NewClient(baseURL, sessionID string, httpClient *http.Client) *Client {
	return &Client{baseURL: baseURL, sessionID: sessionID, http: httpClient}
}

// ListAllEvents retrieves every event after the exclusive timestamp boundary.
func (c *Client) ListAllEvents(ctx context.Context, afterTimestamp int64, pageSize int) ([]Event, error) {
	return nil, errors.New("ListAllEvents is not implemented")
}
