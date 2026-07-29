package taskdiagnosis

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/url"
	"strings"
)

const (
	maxJSONResponseBytes = 1 << 20
	maxBundleBytes       = 4 << 20
	maxArchiveEntries    = 64
	maxArchiveFileBytes  = 1 << 20
	maxArchiveExpanded   = 4 << 20
)

// ErrNotImplemented marks the two incomplete workflow functions.
var ErrNotImplemented = errors.New("task failure diagnosis is not implemented")

// PaceFunc is called only between support-bundle status polls.
type PaceFunc func(ctx context.Context, operationID string, completedPolls int) error

// Config configures one SDDC Manager client.
type Config struct {
	BaseURL     string
	AccessToken string
	HTTPClient  *http.Client
	MaxPolls    int
	Pace        PaceFunc
}

// Client is a focused SDDC Manager REST client.
type Client struct {
	baseURL     string
	accessToken string
	httpClient  *http.Client
	maxPolls    int
	pace        PaceFunc
}

// VCFError is the focused Error schema used by task and API responses.
type VCFError struct {
	ErrorCode          string `json:"errorCode,omitempty"`
	Message            string `json:"message,omitempty"`
	RemediationMessage string `json:"remediationMessage,omitempty"`
	ReferenceToken     string `json:"referenceToken,omitempty"`
}

// Resource is a resource associated with a Task.
type Resource struct {
	ResourceID string `json:"resourceId"`
	FQDN       string `json:"fqdn,omitempty"`
	Type       string `json:"type"`
	Name       string `json:"name,omitempty"`
}

// Task is the focused task response shape.
type Task struct {
	ID                string     `json:"id"`
	Name              string     `json:"name"`
	Type              string     `json:"type,omitempty"`
	Status            string     `json:"status"`
	CreationTimestamp string     `json:"creationTimestamp"`
	Errors            []VCFError `json:"errors,omitempty"`
	Resources         []Resource `json:"resources,omitempty"`
}

// Message is the notification message shape.
type Message struct {
	ID               string   `json:"id,omitempty"`
	LocalizedMessage string   `json:"localizedMessage,omitempty"`
	Arguments        []string `json:"arguments,omitempty"`
}

// NotifiableResource is a resource attached to a Notification.
type NotifiableResource struct {
	ID   string `json:"id,omitempty"`
	Type string `json:"type,omitempty"`
	Name string `json:"name,omitempty"`
}

// NotifiableDomain identifies a notification's domain.
type NotifiableDomain struct {
	ID   string `json:"id,omitempty"`
	Name string `json:"name,omitempty"`
}

// Notification is the getNotifications element shape.
type Notification struct {
	Type                string               `json:"type,omitempty"`
	Severity            string               `json:"severity,omitempty"`
	Message             Message              `json:"message,omitempty"`
	CreationTimestamp   string               `json:"creationTimestamp,omitempty"`
	ExpirationTimestamp string               `json:"expirationTimestamp,omitempty"`
	Resources           []NotifiableResource `json:"resources,omitempty"`
	Domain              *NotifiableDomain    `json:"domain,omitempty"`
}

// Logs models every optional property projected from the official schema.
type Logs struct {
	VCLogs            *bool `json:"vcLogs,omitempty"`
	NSXLogs           *bool `json:"nsxLogs,omitempty"`
	ESXLogs           *bool `json:"esxLogs,omitempty"`
	HCXLogs           *bool `json:"hcxLogs,omitempty"`
	WCPLogs           *bool `json:"wcpLogs,omitempty"`
	SDDCManagerLogs   *bool `json:"sddcManagerLogs,omitempty"`
	APILogs           *bool `json:"apiLogs,omitempty"`
	SystemDebugLogs   *bool `json:"systemDebugLogs,omitempty"`
	VMScreenshots     *bool `json:"vmScreenshots,omitempty"`
	VRALogs           *bool `json:"vraLogs,omitempty"`
	VROpsLogs         *bool `json:"vropsLogs,omitempty"`
	VRLILogs          *bool `json:"vrliLogs,omitempty"`
	VRSLcmLogs        *bool `json:"vrslcmLogs,omitempty"`
	AutomationLogs    *bool `json:"automationLogs,omitempty"`
	OperationsLogs    *bool `json:"operationsLogs,omitempty"`
	OperationsForLogs *bool `json:"operationsForLogs,omitempty"`
	LifecycleLogs     *bool `json:"lifecycleLogs,omitempty"`
	VMSLogs           *bool `json:"vmsLogs,omitempty"`
}

// SupportBundleConfig models optional SoS collection flags.
type SupportBundleConfig struct {
	SkipKnownHostCheck *bool `json:"skipKnownHostCheck,omitempty"`
	Force              *bool `json:"force,omitempty"`
}

// SupportBundleIncludeItems models optional extra reports.
type SupportBundleIncludeItems struct {
	SummaryReport *bool `json:"summaryReport,omitempty"`
	HealthCheck   *bool `json:"healthCheck,omitempty"`
}

// SupportBundleOption models optional collection configuration.
type SupportBundleOption struct {
	Config  *SupportBundleConfig       `json:"config,omitempty"`
	Include *SupportBundleIncludeItems `json:"include,omitempty"`
}

// DomainScope limits support collection to clusters in a domain.
type DomainScope struct {
	DomainName   string   `json:"domainName,omitempty"`
	ClusterNames []string `json:"clusterNames,omitempty"`
}

// SupportBundleScope models optional collection scope.
type SupportBundleScope struct {
	IncludeFreeHosts *bool         `json:"includeFreeHosts,omitempty"`
	Domains          []DomainScope `json:"domains,omitempty"`
}

// SupportBundleSpec is the optional-property request model.
type SupportBundleSpec struct {
	Options *SupportBundleOption `json:"options,omitempty"`
	Scope   *SupportBundleScope  `json:"scope,omitempty"`
	Logs    *Logs                `json:"logs,omitempty"`
}

// SupportBundle is the focused SoS response shape.
type SupportBundle struct {
	Status              string `json:"status,omitempty"`
	CreationTimestamp   string `json:"creationTimestamp,omitempty"`
	Description         string `json:"description,omitempty"`
	BundleAvailable     string `json:"bundleAvailable,omitempty"`
	ID                  string `json:"id,omitempty"`
	CompletionTimestamp string `json:"completionTimestamp,omitempty"`
	BundleName          string `json:"bundleName,omitempty"`
	Size                string `json:"size,omitempty"`
}

// Report contains only evidence that was actually retrieved and correlated.
type Report struct {
	Task           Task
	RelevantEvents []Notification
	Bundle         SupportBundle
	Cause          string
	EvidencePath   string
	EventID        string
}

// APIError is a decoded non-success SDDC Manager Error envelope.
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

// TransportError reports an HTTP transport failure without exposing its text.
type TransportError struct {
	OperationID string
	Cause       error
}

func (e *TransportError) Error() string {
	return e.OperationID + " transport failed"
}

func (e *TransportError) Unwrap() error {
	return e.Cause
}

// ProtocolError reports a malformed or contract-invalid success response.
type ProtocolError struct {
	OperationID string
	Detail      string
}

func (e *ProtocolError) Error() string {
	if e.Detail == "" {
		return e.OperationID + " returned a contract-invalid response"
	}
	return e.OperationID + " returned a contract-invalid response: " + e.Detail
}

// TaskStateError means diagnosis was requested for a task that is not failed.
type TaskStateError struct {
	Task Task
}

func (e *TaskStateError) Error() string {
	return "getTask did not return a failed task"
}

// BundleTerminalError means SoS finished without producing a successful bundle.
type BundleTerminalError struct {
	Bundle SupportBundle
}

func (e *BundleTerminalError) Error() string {
	return "getSupportBundleStatus reported collection failure"
}

// PollLimitError means support-bundle polling exhausted its configured bound.
type PollLimitError struct {
	BundleID   string
	Bound      int
	LastStatus string
}

func (e *PollLimitError) Error() string {
	return "getSupportBundleStatus exhausted its polling limit"
}

// EvidenceError means the retrieved sources did not contain a full correlation.
type EvidenceError struct {
	TaskID string
}

func (e *EvidenceError) Error() string {
	return "downloaded support logs did not correlate with the failed task and events"
}

// NewClient validates configuration without performing network traffic.
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
		strings.IndexFunc(config.AccessToken, isASCIIWhitespace) >= 0 {
		return nil, errors.New("AccessToken must be nonblank and contain no ASCII whitespace")
	}
	if config.MaxPolls < 1 {
		return nil, errors.New("MaxPolls must be at least one")
	}

	httpClient := config.HTTPClient
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	clientCopy := *httpClient
	clientCopy.CheckRedirect = func(_ *http.Request, _ []*http.Request) error {
		return http.ErrUseLastResponse
	}
	pace := config.Pace
	if pace == nil {
		pace = func(context.Context, string, int) error { return nil }
	}
	return &Client{
		baseURL:     parsed.Scheme + "://" + parsed.Host,
		accessToken: config.AccessToken,
		httpClient:  &clientCopy,
		maxPolls:    config.MaxPolls,
		pace:        pace,
	}, nil
}

func isASCIIWhitespace(r rune) bool {
	switch r {
	case ' ', '\t', '\n', '\r', '\v', '\f':
		return true
	default:
		return false
	}
}

// DiagnoseTaskFailure is the incomplete public workflow.
func (c *Client) DiagnoseTaskFailure(
	ctx context.Context,
	taskID string,
) (Report, error) {
	return Report{}, ErrNotImplemented
}

// inspectEvidence is the incomplete bounded tar.gz evidence reader.
func inspectEvidence(
	archive []byte,
	taskID string,
	referenceTokens map[string]struct{},
	eventIDs map[string]struct{},
) (cause string, evidencePath string, eventID string, err error) {
	return "", "", "", ErrNotImplemented
}

func (c *Client) doJSON(
	ctx context.Context,
	operationID string,
	method string,
	targetPath string,
	requestBody any,
	expectedStatus int,
	output any,
) error {
	var body io.Reader
	if requestBody != nil {
		encoded, err := json.Marshal(requestBody)
		if err != nil {
			return &ProtocolError{OperationID: operationID, Detail: "request encoding failed"}
		}
		body = strings.NewReader(string(encoded))
	}
	request, err := http.NewRequestWithContext(ctx, method, c.baseURL+targetPath, body)
	if err != nil {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return ctxErr
		}
		return &ProtocolError{OperationID: operationID, Detail: "request construction failed"}
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Authorization", "Bearer "+c.accessToken)
	if requestBody != nil {
		request.Header.Set("Content-Type", "application/json")
	}

	response, err := c.httpClient.Do(request)
	if err != nil {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return ctxErr
		}
		return &TransportError{OperationID: operationID, Cause: err}
	}
	defer response.Body.Close()
	data, readErr := io.ReadAll(io.LimitReader(response.Body, maxJSONResponseBytes+1))
	if readErr != nil {
		return &TransportError{OperationID: operationID, Cause: readErr}
	}
	if len(data) > maxJSONResponseBytes {
		return &ProtocolError{OperationID: operationID, Detail: "response exceeds size limit"}
	}
	if response.StatusCode != expectedStatus {
		var envelope VCFError
		_ = json.Unmarshal(data, &envelope)
		return &APIError{
			OperationID:        operationID,
			StatusCode:         response.StatusCode,
			ErrorCode:          envelope.ErrorCode,
			Message:            envelope.Message,
			RemediationMessage: envelope.RemediationMessage,
			ReferenceToken:     envelope.ReferenceToken,
		}
	}
	mediaType, _, mediaErr := mime.ParseMediaType(response.Header.Get("Content-Type"))
	if mediaErr != nil || mediaType != "application/json" {
		return &ProtocolError{OperationID: operationID, Detail: "unexpected response media type"}
	}
	if output == nil {
		return nil
	}
	if len(data) == 0 || json.Unmarshal(data, output) != nil {
		return &ProtocolError{OperationID: operationID, Detail: "malformed JSON response"}
	}
	return nil
}

func (c *Client) downloadArchive(
	ctx context.Context,
	targetPath string,
) ([]byte, error) {
	const operationID = "exportSupportBundleByID"
	request, err := http.NewRequestWithContext(
		ctx,
		http.MethodGet,
		c.baseURL+targetPath,
		nil,
	)
	if err != nil {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return nil, ctxErr
		}
		return nil, &ProtocolError{OperationID: operationID, Detail: "request construction failed"}
	}
	request.Header.Set("Accept", "application/octet-stream")
	request.Header.Set("Authorization", "Bearer "+c.accessToken)

	response, err := c.httpClient.Do(request)
	if err != nil {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return nil, ctxErr
		}
		return nil, &TransportError{OperationID: operationID, Cause: err}
	}
	defer response.Body.Close()
	data, readErr := io.ReadAll(io.LimitReader(response.Body, maxBundleBytes+1))
	if readErr != nil {
		return nil, &TransportError{OperationID: operationID, Cause: readErr}
	}
	if response.StatusCode != http.StatusOK {
		var envelope VCFError
		_ = json.Unmarshal(data, &envelope)
		return nil, &APIError{
			OperationID:        operationID,
			StatusCode:         response.StatusCode,
			ErrorCode:          envelope.ErrorCode,
			Message:            envelope.Message,
			RemediationMessage: envelope.RemediationMessage,
			ReferenceToken:     envelope.ReferenceToken,
		}
	}
	if len(data) > maxBundleBytes {
		return nil, &ProtocolError{OperationID: operationID, Detail: "bundle exceeds compressed size limit"}
	}
	mediaType, _, mediaErr := mime.ParseMediaType(response.Header.Get("Content-Type"))
	if mediaErr != nil || mediaType != "application/octet-stream" {
		return nil, &ProtocolError{OperationID: operationID, Detail: "unexpected response media type"}
	}
	return data, nil
}
