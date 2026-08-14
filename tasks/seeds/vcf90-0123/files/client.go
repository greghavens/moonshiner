// Package vsandp implements the small part of the VCF 9.0 vSAN Data
// Protection REST API used by the backup controller.
package vsandp

import (
	"context"
	"errors"
	"net/http"
	"strings"
)

// TokenSource supplies the current session token and can refresh it after the
// service reports that it has expired.
type TokenSource interface {
	Token(context.Context) (string, error)
	Refresh(context.Context) (string, error)
}

// RetentionPeriod is the optional retention member of a manual snapshot
// request. Unit is one of MINUTE, HOUR, DAY, WEEK, or MONTH.
type RetentionPeriod struct {
	Unit     string `json:"unit"`
	Duration int64  `json:"duration"`
}

// CreateSnapshotSpec is the request for a one-time protection-group snapshot.
type CreateSnapshotSpec struct {
	Name      string           `json:"name"`
	Retention *RetentionPeriod `json:"retention,omitempty"`
}

// LocalizableMessage is the message shape embedded in task information.
type LocalizableMessage struct {
	ID             string   `json:"id"`
	DefaultMessage string   `json:"default_message"`
	Args           []string `json:"args"`
}

// TaskInfo contains the task fields used by the controller.
type TaskInfo struct {
	Cancelable  bool               `json:"cancelable"`
	Description LocalizableMessage `json:"description"`
	Operation   string             `json:"operation"`
	Service     string             `json:"service"`
	Status      string             `json:"status"`
}

// SnapshotResult retains both the task identifier returned by the create call
// and its final information.
type SnapshotResult struct {
	TaskID string
	Task   TaskInfo
}

// Client calls the contract-pinned vSAN Data Protection endpoints.
type Client struct {
	baseURL    string
	httpClient *http.Client
	tokens     TokenSource
}

// NewClient creates a client. baseURL includes the contract's /api base path.
func NewClient(baseURL string, httpClient *http.Client, tokens TokenSource) *Client {
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{
		baseURL:    strings.TrimRight(baseURL, "/"),
		httpClient: httpClient,
		tokens:     tokens,
	}
}

// ErrNotImplemented marks the missing snapshot workflow.
var ErrNotImplemented = errors.New("vSAN Data Protection snapshot workflow is not implemented")

// CreateSnapshotAndWait creates one snapshot and follows its task to a terminal
// state. The implementation is intentionally left for the integration ticket.
func (c *Client) CreateSnapshotAndWait(
	ctx context.Context,
	clusterID string,
	protectionGroupID string,
	spec CreateSnapshotSpec,
) (SnapshotResult, error) {
	return SnapshotResult{}, ErrNotImplemented
}
