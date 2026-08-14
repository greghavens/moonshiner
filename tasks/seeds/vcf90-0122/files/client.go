package vsandp

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"time"
)

// TaskStatus is the status reported by the vSAN Data Protection task API.
type TaskStatus string

const (
	TaskPending   TaskStatus = "PENDING"
	TaskRunning   TaskStatus = "RUNNING"
	TaskBlocked   TaskStatus = "BLOCKED"
	TaskSucceeded TaskStatus = "SUCCEEDED"
	TaskFailed    TaskStatus = "FAILED"
)

// RetentionPeriod is the optional retention for a one-time snapshot.
type RetentionPeriod struct {
	Unit     string `json:"unit"`
	Duration int64  `json:"duration"`
}

// SnapshotCreateSpec describes a manual protection-group snapshot.
type SnapshotCreateSpec struct {
	Name      string           `json:"name"`
	Retention *RetentionPeriod `json:"retention,omitempty"`
}

// LocalizableMessage is the message shape returned in task information.
type LocalizableMessage struct {
	ID             string   `json:"id"`
	DefaultMessage string   `json:"default_message"`
	Args           []string `json:"args"`
}

// TaskInfo contains the fields used while waiting for an asynchronous task.
type TaskInfo struct {
	Status      TaskStatus         `json:"status"`
	Cancelable  bool               `json:"cancelable"`
	Description LocalizableMessage `json:"description"`
	Service     string             `json:"service"`
	Operation   string             `json:"operation"`
	Error       map[string]any     `json:"error,omitempty"`
}

// TaskFailedError reports a terminal FAILED task.
type TaskFailedError struct {
	Task TaskInfo
}

func (e *TaskFailedError) Error() string {
	return fmt.Sprintf("vSAN Data Protection task failed: %s", e.Task.Operation)
}

// Client invokes the two operations needed by CreateSnapshotAndWait.
type Client struct {
	baseURL      string
	sessionID    string
	httpClient   *http.Client
	pollInterval time.Duration
}

// NewClient constructs a client. baseURL includes the API base path (normally /api).
func NewClient(baseURL, sessionID string, httpClient *http.Client, pollInterval time.Duration) *Client {
	return &Client{
		baseURL:      baseURL,
		sessionID:    sessionID,
		httpClient:   httpClient,
		pollInterval: pollInterval,
	}
}

// CreateSnapshotAndWait creates a protection-group snapshot and waits for its task.
func (c *Client) CreateSnapshotAndWait(ctx context.Context, clusterID, protectionGroupID string, spec SnapshotCreateSpec) (TaskInfo, error) {
	return TaskInfo{}, errors.New("CreateSnapshotAndWait is not implemented")
}
