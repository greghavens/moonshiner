// Package hostrefresh implements the focused SDDC Manager host-refresh
// workflow described by the protected OpenAPI-derived contract.
package hostrefresh

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
)

const maxResponseBytes = 1 << 20

// ErrNotImplemented is returned by the incomplete workflow.
var ErrNotImplemented = errors.New("host refresh workflow is not implemented")

// PaceFunc is invoked only between consecutive getTask requests.
type PaceFunc func(
	ctx context.Context,
	operationID string,
	completedPolls int,
) error

// Config configures a Client.
type Config struct {
	BaseURL     string
	AccessToken string
	HTTPClient  *http.Client
	MaxPolls    int
	Pace        PaceFunc
}

// RefreshRequest is the public host update request. A nil ForceRefresh omits
// hostsRefreshSpec. A non-nil pointer sends the pointed-to boolean verbatim.
type RefreshRequest struct {
	HostIDs      []string
	ForceRefresh *bool
}

// VCFError is the focused SDDC Manager Error shape.
type VCFError struct {
	ErrorCode          string `json:"errorCode,omitempty"`
	Message            string `json:"message,omitempty"`
	RemediationMessage string `json:"remediationMessage,omitempty"`
	ReferenceToken     string `json:"referenceToken,omitempty"`
}

// Task is the focused asynchronous Task shape.
type Task struct {
	ID                string     `json:"id"`
	Name              string     `json:"name"`
	Type              string     `json:"type,omitempty"`
	Status            string     `json:"status"`
	CreationTimestamp string     `json:"creationTimestamp"`
	Errors            []VCFError `json:"errors,omitempty"`
}

// Host is the stable public projection of one getHosts element.
type Host struct {
	ID     string `json:"id"`
	FQDN   string `json:"fqdn"`
	Status string `json:"status"`
}

// PageMetadata is the focused collection metadata shape.
type PageMetadata struct {
	PageNumber    int `json:"pageNumber"`
	PageSize      int `json:"pageSize"`
	TotalElements int `json:"totalElements"`
	TotalPages    int `json:"totalPages"`
}

// HostPage is the focused getHosts success shape.
type HostPage struct {
	Elements     []Host       `json:"elements"`
	PageMetadata PageMetadata `json:"pageMetadata"`
}

// RefreshResult contains the terminal task and the deterministically ordered
// host collection fetched after task success.
type RefreshResult struct {
	Task  Task
	Hosts []Host
}

// APIError preserves the structured non-success response without exposing its
// body or decoded messages through Error.
type APIError struct {
	OperationID        string
	Status             int
	ErrorCode          string
	Message            string
	RemediationMessage string
	ReferenceToken     string
}

func (e *APIError) Error() string {
	if e == nil {
		return "SDDC Manager API request failed"
	}
	return fmt.Sprintf(
		"SDDC Manager operation %s failed with HTTP %d",
		e.OperationID,
		e.Status,
	)
}

// TransportError wraps a transport failure while keeping its text redacted.
type TransportError struct {
	OperationID string
	Err         error
}

func (e *TransportError) Error() string {
	if e == nil {
		return "SDDC Manager transport failed"
	}
	return fmt.Sprintf("SDDC Manager operation %s transport failed", e.OperationID)
}

func (e *TransportError) Unwrap() error {
	if e == nil {
		return nil
	}
	return e.Err
}

// ProtocolError reports a malformed success response or unknown task status.
type ProtocolError struct {
	OperationID string
	Reason      string
}

func (e *ProtocolError) Error() string {
	if e == nil {
		return "SDDC Manager protocol error"
	}
	return fmt.Sprintf(
		"SDDC Manager operation %s violated the response contract: %s",
		e.OperationID,
		e.Reason,
	)
}

// TaskTerminalError reports a terminal task that did not succeed.
type TaskTerminalError struct {
	Task Task
}

func (e *TaskTerminalError) Error() string {
	if e == nil {
		return "host refresh task failed"
	}
	return fmt.Sprintf("host refresh task reached terminal status %q", e.Task.Status)
}

// PollLimitError reports exhaustion of the bounded task polling budget.
type PollLimitError struct {
	TaskID     string
	Limit      int
	LastStatus string
}

func (e *PollLimitError) Error() string {
	if e == nil {
		return "host refresh polling limit reached"
	}
	return fmt.Sprintf(
		"host refresh task %q did not finish within %d polls",
		e.TaskID,
		e.Limit,
	)
}

// Client is a focused client for updateHosts, getTask, and getHosts.
type Client struct {
	baseURL     string
	accessToken string
	httpClient  *http.Client
	maxPolls    int
	pace        PaceFunc
}

// NewClient validates configuration and performs no network traffic.
func NewClient(config Config) (*Client, error) {
	parsed, err := url.Parse(config.BaseURL)
	if err != nil ||
		(parsed.Scheme != "http" && parsed.Scheme != "https") ||
		parsed.Host == "" ||
		parsed.User != nil ||
		parsed.RawQuery != "" ||
		parsed.ForceQuery ||
		parsed.Fragment != "" ||
		(parsed.Path != "" && parsed.Path != "/") {
		return nil, errors.New("BaseURL must be an HTTP(S) origin")
	}
	if strings.TrimSpace(config.AccessToken) == "" ||
		containsASCIIWhitespace(config.AccessToken) {
		return nil, errors.New("AccessToken must be nonblank and contain no ASCII whitespace")
	}
	if config.MaxPolls < 1 {
		return nil, errors.New("MaxPolls must be at least 1")
	}

	baseClient := config.HTTPClient
	if baseClient == nil {
		baseClient = http.DefaultClient
	}
	clientCopy := *baseClient
	clientCopy.CheckRedirect = func(
		_ *http.Request,
		_ []*http.Request,
	) error {
		return http.ErrUseLastResponse
	}
	pace := config.Pace
	if pace == nil {
		pace = func(context.Context, string, int) error { return nil }
	}

	return &Client{
		baseURL:     strings.TrimSuffix(parsed.String(), "/"),
		accessToken: config.AccessToken,
		httpClient:  &clientCopy,
		maxPolls:    config.MaxPolls,
		pace:        pace,
	}, nil
}

// RefreshHosts submits updateHosts, polls getTask to a terminal state, then
// retrieves and sorts getHosts output.
func (c *Client) RefreshHosts(
	ctx context.Context,
	request RefreshRequest,
) (RefreshResult, error) {
	return RefreshResult{}, ErrNotImplemented
}

type hostsRefreshWire struct {
	ForceRefresh bool `json:"forceRefresh"`
}

type hostsUpdateWire struct {
	HostIDs          []string          `json:"hostIds"`
	HostsRefreshSpec *hostsRefreshWire `json:"hostsRefreshSpec,omitempty"`
}

func validateRefreshRequest(request RefreshRequest) error {
	return ErrNotImplemented
}

func (c *Client) submitRefresh(
	ctx context.Context,
	request RefreshRequest,
) (Task, error) {
	return Task{}, ErrNotImplemented
}

func (c *Client) waitForTask(
	ctx context.Context,
	accepted Task,
) (Task, error) {
	return Task{}, ErrNotImplemented
}

func (c *Client) listHosts(ctx context.Context) ([]Host, error) {
	return nil, ErrNotImplemented
}

func (c *Client) doJSON(
	ctx context.Context,
	operationID string,
	method string,
	path string,
	body any,
	successStatus int,
	out any,
) error {
	return ErrNotImplemented
}

func validatePolledTask(task Task, taskID string) error {
	if task.ID != taskID {
		return &ProtocolError{
			OperationID: "getTask",
			Reason:      "task id did not match the requested id",
		}
	}
	if strings.TrimSpace(task.Name) == "" ||
		strings.TrimSpace(task.Status) == "" ||
		strings.TrimSpace(task.CreationTimestamp) == "" {
		return &ProtocolError{
			OperationID: "getTask",
			Reason:      "required Task fields were missing",
		}
	}
	return nil
}

func normalizeTaskStatus(status string) string {
	return strings.ToUpper(strings.ReplaceAll(strings.TrimSpace(status), " ", "_"))
}

func decodeOneJSON(data []byte, out any) error {
	decoder := json.NewDecoder(bytes.NewReader(data))
	if err := decoder.Decode(out); err != nil {
		return err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return errors.New("JSON response contains trailing data")
	}
	return nil
}

func containsASCIIWhitespace(value string) bool {
	for index := 0; index < len(value); index++ {
		switch value[index] {
		case ' ', '\t', '\n', '\r', '\v', '\f':
			return true
		}
	}
	return false
}
