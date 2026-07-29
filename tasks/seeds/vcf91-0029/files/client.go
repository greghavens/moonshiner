// Package systembaseline applies a focused, ordered VCF 9.1 SDDC Manager
// system baseline and preserves the outcome of every step that was attempted.
package systembaseline

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

// ErrNotImplemented is returned by the incomplete workflow scaffold.
var ErrNotImplemented = errors.New("system baseline workflow is not implemented")

// Config configures one SDDC Manager client.
type Config struct {
	BaseURL     string
	AccessToken string
	HTTPClient  *http.Client
	MaxPolls    int
	Pace        func(context.Context, string, int) error
}

// ProxyConfiguration is the writable projection of the specification's
// ProxyConfiguration schema. Pointers distinguish unset values from explicit
// false, zero, and empty values.
type ProxyConfiguration struct {
	IsEnabled        *bool   `json:"isEnabled,omitempty"`
	Host             *string `json:"host,omitempty"`
	Port             *int32  `json:"port,omitempty"`
	TransferProtocol *string `json:"transferProtocol,omitempty"`
	Username         *string `json:"username,omitempty"`
	Password         *string `json:"password,omitempty"`
	IsAuthenticated  *bool   `json:"isAuthenticated,omitempty"`
}

// CeipUpdateSpec is the setCeipStatus request body.
type CeipUpdateSpec struct {
	Status string `json:"status"`
}

// BaselineSpec contains the two changes, applied in declaration order.
type BaselineSpec struct {
	Proxy ProxyConfiguration
	CEIP  CeipUpdateSpec
}

// VCFError is the focused common SDDC Manager Error envelope.
type VCFError struct {
	ErrorCode          string `json:"errorCode,omitempty"`
	Message            string `json:"message,omitempty"`
	RemediationMessage string `json:"remediationMessage,omitempty"`
	ReferenceToken     string `json:"referenceToken,omitempty"`
}

// Task is the focused asynchronous Task response.
type Task struct {
	ID                  string     `json:"id"`
	Name                string     `json:"name"`
	Type                string     `json:"type,omitempty"`
	Status              string     `json:"status"`
	CreationTimestamp   string     `json:"creationTimestamp"`
	CompletionTimestamp string     `json:"completionTimestamp,omitempty"`
	Errors              []VCFError `json:"errors,omitempty"`
}

// StepResult reports the last confirmed state of one submitted change.
type StepResult struct {
	OperationID string
	TaskID      string
	Status      string
	PollCount   int
}

// Report preserves ordered outcomes, including earlier successful steps when a
// later step fails.
type Report struct {
	Steps []StepResult
}

// APIError represents a non-success response from one named operation.
type APIError struct {
	OperationID        string
	StatusCode         int
	ErrorCode          string
	Message            string
	RemediationMessage string
	ReferenceToken     string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s failed with HTTP status %d", e.OperationID, e.StatusCode)
}

// TaskTerminalError represents an unsuccessful terminal Task.
type TaskTerminalError struct {
	OperationID string
	Task        Task
}

func (e *TaskTerminalError) Error() string {
	return e.OperationID + " reached an unsuccessful terminal task state"
}

// PollLimitError reports a task that did not terminate within MaxPolls.
type PollLimitError struct {
	OperationID string
	TaskID      string
	MaxPolls    int
	LastStatus  string
}

func (e *PollLimitError) Error() string {
	return fmt.Sprintf("%s did not finish within %d task polls", e.OperationID, e.MaxPolls)
}

// ProtocolError reports a response that violates the focused contract.
type ProtocolError struct {
	OperationID string
	Reason      string
}

func (e *ProtocolError) Error() string {
	return e.OperationID + " response violated the focused SDDC Manager contract"
}

// TransportError reports a transport failure without exposing the underlying
// error, request, or bearer token.
type TransportError struct {
	OperationID string
}

func (e *TransportError) Error() string {
	return e.OperationID + " transport failure"
}

// Client applies the two-step system baseline.
type Client struct {
	baseURL     string
	accessToken string
	httpClient  *http.Client
	maxPolls    int
	pace        func(context.Context, string, int) error
}

// NewClient validates configuration without performing network I/O.
func NewClient(config Config) (*Client, error) {
	parsed, err := url.Parse(config.BaseURL)
	if err != nil {
		return nil, errors.New("BaseURL must be an HTTP(S) origin")
	}
	scheme := strings.ToLower(parsed.Scheme)
	if (scheme != "http" && scheme != "https") ||
		parsed.Host == "" ||
		parsed.Hostname() == "" ||
		parsed.User != nil ||
		parsed.Opaque != "" ||
		(parsed.Path != "" && parsed.Path != "/") ||
		(parsed.EscapedPath() != "" && parsed.EscapedPath() != "/") ||
		parsed.RawQuery != "" ||
		parsed.ForceQuery ||
		parsed.Fragment != "" {
		return nil, errors.New("BaseURL must be an HTTP(S) origin")
	}
	if config.AccessToken == "" ||
		strings.ContainsAny(config.AccessToken, " \t\r\n\v\f") {
		return nil, errors.New("AccessToken must be non-empty and contain no ASCII whitespace")
	}
	if config.MaxPolls < 1 {
		return nil, errors.New("MaxPolls must be at least one")
	}

	httpClient := config.HTTPClient
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	httpClientCopy := *httpClient
	httpClientCopy.CheckRedirect = func(_ *http.Request, _ []*http.Request) error {
		return http.ErrUseLastResponse
	}
	pace := config.Pace
	if pace == nil {
		pace = func(context.Context, string, int) error { return nil }
	}
	return &Client{
		baseURL:     scheme + "://" + parsed.Host,
		accessToken: config.AccessToken,
		httpClient:  &httpClientCopy,
		maxPolls:    config.MaxPolls,
		pace:        pace,
	}, nil
}

// ApplySystemBaseline applies and waits for the proxy change before starting
// the CEIP change. It always returns all known step states.
func (c *Client) ApplySystemBaseline(
	ctx context.Context,
	spec BaselineSpec,
) (Report, error) {
	var report Report
	return report, ErrNotImplemented
}

func validateBaseline(spec BaselineSpec) error {
	proxy := spec.Proxy
	if proxy.IsEnabled == nil {
		return errors.New("Proxy.IsEnabled must be set")
	}
	if proxy.TransferProtocol != nil &&
		*proxy.TransferProtocol != "HTTP" &&
		*proxy.TransferProtocol != "HTTPS" {
		return errors.New("Proxy.TransferProtocol must be HTTP or HTTPS")
	}
	if *proxy.IsEnabled {
		if proxy.Host == nil || strings.TrimSpace(*proxy.Host) == "" {
			return errors.New("Proxy.Host must be set when the proxy is enabled")
		}
		if proxy.Port == nil || *proxy.Port < 1 || *proxy.Port > 65535 {
			return errors.New("Proxy.Port must be valid when the proxy is enabled")
		}
	}
	if proxy.IsAuthenticated != nil && *proxy.IsAuthenticated {
		if proxy.Username == nil || strings.TrimSpace(*proxy.Username) == "" ||
			proxy.Password == nil || *proxy.Password == "" {
			return errors.New("Proxy credentials must be set when authentication is enabled")
		}
	}
	if spec.CEIP.Status != "ENABLE" && spec.CEIP.Status != "DISABLE" {
		return errors.New("CEIP.Status must be ENABLE or DISABLE")
	}
	return nil
}

func (c *Client) submitAndWait(
	ctx context.Context,
	operationID string,
	path string,
	body any,
) (Task, int, bool, error) {
	responseBody, err := c.request(
		ctx,
		operationID,
		http.MethodPatch,
		path,
		body,
		http.StatusAccepted,
	)
	if err != nil {
		return Task{}, 0, false, err
	}
	task, err := decodeTask(operationID, responseBody)
	if err != nil {
		return Task{}, 0, false, err
	}
	if task.ID == "" {
		return Task{}, 0, false, &ProtocolError{
			OperationID: operationID,
			Reason:      "accepted Task is missing id",
		}
	}
	last, polls, err := c.waitForTask(ctx, operationID, task)
	return last, polls, true, err
}

func (c *Client) waitForTask(
	ctx context.Context,
	operationID string,
	accepted Task,
) (Task, int, error) {
	last := accepted
	for completedPolls := 1; completedPolls <= c.maxPolls; completedPolls++ {
		if err := ctx.Err(); err != nil {
			return last, completedPolls - 1, err
		}
		path := "/v1/tasks/" + url.PathEscape(accepted.ID)
		responseBody, err := c.request(
			ctx,
			"getTask",
			http.MethodGet,
			path,
			nil,
			http.StatusOK,
		)
		if err != nil {
			return last, completedPolls, err
		}
		task, err := decodeTask("getTask", responseBody)
		if err != nil {
			return last, completedPolls, err
		}
		if task.ID == "" || task.ID != accepted.ID || task.Status == "" {
			return task, completedPolls, &ProtocolError{
				OperationID: "getTask",
				Reason:      "Task identity or status does not match the request",
			}
		}
		last = task
		switch normalizedStatus(task.Status) {
		case "SUCCESSFUL", "COMPLETED_WITH_WARNING":
			return task, completedPolls, nil
		case "FAILED", "CANCELLED", "SKIPPED", "TIMED_OUT":
			return task, completedPolls, &TaskTerminalError{
				OperationID: operationID,
				Task:        task,
			}
		case "PENDING", "IN_PROGRESS", "QUEUED":
			if completedPolls == c.maxPolls {
				return task, completedPolls, &PollLimitError{
					OperationID: operationID,
					TaskID:      accepted.ID,
					MaxPolls:    c.maxPolls,
					LastStatus:  task.Status,
				}
			}
		default:
			return task, completedPolls, &ProtocolError{
				OperationID: "getTask",
				Reason:      "unrecognized Task status " + task.Status,
			}
		}
		if err := c.pace(ctx, operationID, completedPolls); err != nil {
			return task, completedPolls, err
		}
		if err := ctx.Err(); err != nil {
			return task, completedPolls, err
		}
	}
	panic("unreachable")
}

func (c *Client) request(
	ctx context.Context,
	operationID string,
	method string,
	path string,
	body any,
	expectedStatus int,
) ([]byte, error) {
	var reader io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return nil, &ProtocolError{
				OperationID: operationID,
				Reason:      "request body cannot be encoded",
			}
		}
		reader = bytes.NewReader(encoded)
	}
	request, err := http.NewRequestWithContext(
		ctx,
		method,
		c.baseURL+path,
		reader,
	)
	if err != nil {
		if contextError := ctx.Err(); contextError != nil {
			return nil, contextError
		}
		return nil, &TransportError{OperationID: operationID}
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Authorization", "Bearer "+c.accessToken)
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}

	response, err := c.httpClient.Do(request)
	if err != nil {
		if response != nil && response.Body != nil {
			_ = response.Body.Close()
		}
		if contextError := ctx.Err(); contextError != nil {
			return nil, contextError
		}
		return nil, &TransportError{OperationID: operationID}
	}
	responseBody, readErr := readAndClose(response)
	if readErr != nil {
		if contextError := ctx.Err(); contextError != nil {
			return nil, contextError
		}
		return nil, &TransportError{OperationID: operationID}
	}
	if response.StatusCode != expectedStatus {
		return nil, decodeAPIError(operationID, response.StatusCode, responseBody)
	}
	if len(responseBody) > maxResponseBytes {
		return nil, &ProtocolError{
			OperationID: operationID,
			Reason:      "response exceeded the size limit",
		}
	}
	return responseBody, nil
}

func readAndClose(response *http.Response) ([]byte, error) {
	defer response.Body.Close()
	return io.ReadAll(io.LimitReader(response.Body, maxResponseBytes+1))
}

func decodeAPIError(operationID string, statusCode int, body []byte) *APIError {
	var envelope VCFError
	if len(body) <= maxResponseBytes {
		_ = json.Unmarshal(body, &envelope)
	}
	return &APIError{
		OperationID:        operationID,
		StatusCode:         statusCode,
		ErrorCode:          envelope.ErrorCode,
		Message:            envelope.Message,
		RemediationMessage: envelope.RemediationMessage,
		ReferenceToken:     envelope.ReferenceToken,
	}
}

func decodeTask(operationID string, body []byte) (Task, error) {
	var task Task
	if err := json.Unmarshal(body, &task); err != nil {
		return Task{}, &ProtocolError{
			OperationID: operationID,
			Reason:      "response is not a Task object",
		}
	}
	return task, nil
}

func normalizedStatus(value string) string {
	return strings.ReplaceAll(strings.ToUpper(strings.TrimSpace(value)), " ", "_")
}

func stepResult(operationID string, task Task, pollCount int) StepResult {
	return StepResult{
		OperationID: operationID,
		TaskID:      task.ID,
		Status:      task.Status,
		PollCount:   pollCount,
	}
}
