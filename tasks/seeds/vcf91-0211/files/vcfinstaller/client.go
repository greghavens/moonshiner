package vcfinstaller

import (
	"context"
	"errors"
	"net/http"
)

// Task is the focused getTasks representation used by this package.
type Task struct {
	ID                string  `json:"id"`
	Name              string  `json:"name"`
	Type              *string `json:"type,omitempty"`
	Status            string  `json:"status"`
	CreationTimestamp string  `json:"creationTimestamp"`
}

// ListTasksOptions contains getTasks filters. Nil pointers mean omitted.
type ListTasksOptions struct {
	PageSize       int
	Limit          *int
	TaskStatus     *string
	TaskType       *string
	ResourceID     *string
	ResourceType   *string
	CompletedAfter *int64
	OrderDirection *string
	OrderBy        *string
	TaskName       *string
	DoLiveRefresh  *bool
}

// APIError is a decoded non-success response.
type APIError struct {
	StatusCode int
	ErrorCode  string
	Message    string
}

func (e *APIError) Error() string { return "VCF Installer API request failed" }

// ProtocolError reports a malformed or incoherent success response.
type ProtocolError struct{ Reason string }

func (e *ProtocolError) Error() string { return "VCF Installer protocol error: " + e.Reason }

// Client calls the VCF Installer API.
type Client struct{}

// NewClient constructs a client. It is intentionally incomplete.
func NewClient(baseURL, accessToken string, httpClient *http.Client) (*Client, error) {
	return nil, errors.New("TODO: implement NewClient")
}

// ListAllTasks retrieves and orders the complete task collection.
func (c *Client) ListAllTasks(ctx context.Context, options ListTasksOptions) ([]Task, error) {
	return nil, errors.New("TODO: implement ListAllTasks")
}
