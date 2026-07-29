package nsxchange

import (
	"context"
	"errors"
	"fmt"
	"net/http"
)

var ErrNotImplemented = errors.New("nsxchange: not implemented")

// HTTPDoer is satisfied by *http.Client.
type HTTPDoer interface {
	Do(*http.Request) (*http.Response, error)
}

type Client struct {
	baseURL string
	http    HTTPDoer
}

func NewClient(baseURL string, httpClient HTTPDoer) (*Client, error) {
	if baseURL == "" {
		return nil, errors.New("nsxchange: base URL is required")
	}
	if httpClient == nil {
		return nil, errors.New("nsxchange: HTTP client is required")
	}
	return &Client{baseURL: baseURL, http: httpClient}, nil
}

// APIError is returned for a non-2xx NSX response.
type APIError struct {
	OperationID string `json:"-"`
	StatusCode  int    `json:"-"`
	ErrorCode   int    `json:"error_code"`
	Message     string `json:"error_message"`
	ModuleName  string `json:"module_name,omitempty"`
	Details     string `json:"details,omitempty"`
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s: HTTP %d: %s", e.OperationID, e.StatusCode, e.Message)
}

func (c *Client) patch(ctx context.Context, operationID, path string, body any) (int, error) {
	return 0, ErrNotImplemented
}
