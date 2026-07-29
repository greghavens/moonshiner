// Package credentialgate coordinates safe, contract-focused VMware Cloud
// Foundation 9.1 SDDC Manager password rotation.
package credentialgate

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
	"sync"
	"unicode"
)

const maxResponseBytes = 1 << 20

// ErrNotImplemented is returned by the incomplete lease and rotation methods.
var ErrNotImplemented = errors.New("credential gate is not implemented")

// Config configures a credential manager.
type Config struct {
	BaseURL         string
	AccessToken     string
	CurrentPassword string
	HTTPClient      *http.Client
	MaxPolls        int
	Pace            func(context.Context, string, int) error
}

// RotationTarget identifies the one account whose password SDDC Manager will
// generate and rotate. Pointer fields preserve optional-field presence.
type RotationTarget struct {
	ResourceName   *string
	ResourceID     *string
	ResourceType   string
	CredentialType *string
	AccountType    *string
	Username       string
}

// VCFError is the focused common SDDC Manager Error envelope.
type VCFError struct {
	ErrorCode          string `json:"errorCode,omitempty"`
	Message            string `json:"message,omitempty"`
	RemediationMessage string `json:"remediationMessage,omitempty"`
	ReferenceToken     string `json:"referenceToken,omitempty"`
}

// Task is the accepted asynchronous task returned by the rotation PATCH.
type Task struct {
	ID                  string     `json:"id"`
	Name                string     `json:"name"`
	Type                string     `json:"type,omitempty"`
	Status              string     `json:"status"`
	CreationTimestamp   string     `json:"creationTimestamp"`
	CompletionTimestamp string     `json:"completionTimestamp,omitempty"`
	Errors              []VCFError `json:"errors,omitempty"`
}

// CredentialsSubTask is one resource-account result in a credential task.
type CredentialsSubTask struct {
	ID                  string               `json:"id,omitempty"`
	ResourceName        string               `json:"resourceName,omitempty"`
	Name                string               `json:"name"`
	Description         string               `json:"description"`
	CreationTimestamp   string               `json:"creationTimestamp"`
	CompletionTimestamp string               `json:"completionTimestamp,omitempty"`
	Status              string               `json:"status"`
	DependentSubTasks   []CredentialsSubTask `json:"dependentSubTasks,omitempty"`
	Errors              []VCFError           `json:"errors,omitempty"`
	OldPassword         string               `json:"oldPassword,omitempty"`
	NewPassword         string               `json:"newPassword,omitempty"`
	EntityType          string               `json:"entityType,omitempty"`
	Username            string               `json:"username,omitempty"`
	CredentialType      string               `json:"credentialType,omitempty"`
}

// CredentialsTask is the getCredentialsTask response.
type CredentialsTask struct {
	ID                  string               `json:"id"`
	Name                string               `json:"name"`
	Type                string               `json:"type"`
	CreationTimestamp   string               `json:"creationTimestamp"`
	CompletionTimestamp string               `json:"completionTimestamp,omitempty"`
	Status              string               `json:"status"`
	SubTasks            []CredentialsSubTask `json:"subTasks,omitempty"`
	Errors              []VCFError           `json:"errors,omitempty"`
	IsAutoRotate        bool                 `json:"isAutoRotate,omitempty"`
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

// TaskTerminalError represents an unsuccessful terminal credential task.
type TaskTerminalError struct {
	Task CredentialsTask
}

func (e *TaskTerminalError) Error() string {
	return "credential rotation reached an unsuccessful terminal task state"
}

// PollLimitError reports a task that did not terminate within MaxPolls.
type PollLimitError struct {
	TaskID     string
	MaxPolls   int
	LastStatus string
}

func (e *PollLimitError) Error() string {
	return fmt.Sprintf("credential rotation did not finish within %d task polls", e.MaxPolls)
}

// ProtocolError reports a response that violates the focused contract.
type ProtocolError struct {
	OperationID string
	Reason      string
}

func (e *ProtocolError) Error() string {
	return e.OperationID + " response violated the focused SDDC Manager contract"
}

// TransportError reports a transport failure without exposing its cause.
type TransportError struct {
	OperationID string
}

func (e *TransportError) Error() string {
	return e.OperationID + " transport failure"
}

// Manager owns the password and coordinates leases with rotation.
type Manager struct {
	baseURL     string
	accessToken string
	httpClient  *http.Client
	maxPolls    int
	pace        func(context.Context, string, int) error

	mu              sync.Mutex
	changed         chan struct{}
	currentPassword string
	activeLeases    int
	rotating        bool
}

// Lease protects one observed password until Release is called.
type Lease struct {
	manager  *Manager
	password string
	once     sync.Once
}

// Password returns the immutable password associated with this lease.
func (l *Lease) Password() string {
	if l == nil {
		return ""
	}
	return l.password
}

// Release ends the lease. Repeated calls are safe.
func (l *Lease) Release() {
	if l == nil {
		return
	}
	l.once.Do(func() {
		if l.manager == nil {
			return
		}
		l.manager.mu.Lock()
		if l.manager.activeLeases > 0 {
			l.manager.activeLeases--
			l.manager.broadcastLocked()
		}
		l.manager.mu.Unlock()
	})
}

// NewManager validates configuration without performing network I/O.
func NewManager(config Config) (*Manager, error) {
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
		strings.IndexFunc(config.AccessToken, unicode.IsSpace) >= 0 {
		return nil, errors.New("AccessToken must be non-empty and contain no whitespace")
	}
	if config.CurrentPassword == "" {
		return nil, errors.New("CurrentPassword must be non-empty")
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
	return &Manager{
		baseURL:         scheme + "://" + parsed.Host,
		accessToken:     config.AccessToken,
		httpClient:      &httpClientCopy,
		maxPolls:        config.MaxPolls,
		pace:            pace,
		changed:         make(chan struct{}),
		currentPassword: config.CurrentPassword,
	}, nil
}

// Acquire obtains a lease on the current password. It waits interruptibly
// while a rotation owns the gate.
func (m *Manager) Acquire(ctx context.Context) (*Lease, error) {
	return nil, ErrNotImplemented
}

// Rotate drains old-password leases, requests a generated password, and
// atomically publishes it before reopening the lease gate.
func (m *Manager) Rotate(
	ctx context.Context,
	target RotationTarget,
) (CredentialsTask, error) {
	return CredentialsTask{}, ErrNotImplemented
}

func (m *Manager) beginRotation(ctx context.Context) error {
	for {
		m.mu.Lock()
		if err := ctx.Err(); err != nil {
			m.mu.Unlock()
			return err
		}
		if !m.rotating {
			m.rotating = true
			m.broadcastLocked()
			m.mu.Unlock()
			return nil
		}
		changed := m.changed
		m.mu.Unlock()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-changed:
		}
	}
}

func (m *Manager) waitForLeases(ctx context.Context) error {
	for {
		m.mu.Lock()
		if err := ctx.Err(); err != nil {
			m.mu.Unlock()
			return err
		}
		if m.activeLeases == 0 {
			m.mu.Unlock()
			return nil
		}
		changed := m.changed
		m.mu.Unlock()
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-changed:
		}
	}
}

func (m *Manager) endRotation() {
	m.mu.Lock()
	m.rotating = false
	m.broadcastLocked()
	m.mu.Unlock()
}

func (m *Manager) broadcastLocked() {
	close(m.changed)
	m.changed = make(chan struct{})
}

func validateTarget(target RotationTarget) error {
	if target.ResourceType == "" ||
		strings.TrimSpace(target.ResourceType) != target.ResourceType {
		return errors.New("ResourceType must be nonblank with no surrounding whitespace")
	}
	if target.Username == "" ||
		strings.TrimSpace(target.Username) != target.Username {
		return errors.New("Username must be nonblank with no surrounding whitespace")
	}
	for name, value := range map[string]*string{
		"ResourceName":   target.ResourceName,
		"ResourceID":     target.ResourceID,
		"CredentialType": target.CredentialType,
		"AccountType":    target.AccountType,
	} {
		if value != nil &&
			(*value == "" || strings.TrimSpace(*value) != *value) {
			return errors.New(name + " must be nonblank with no surrounding whitespace when set")
		}
	}
	return nil
}

type credentialsUpdateSpec struct {
	OperationType    string                               `json:"operationType"`
	Elements         []resourceCredentials                `json:"elements"`
	AutoRotatePolicy *autoRotateCredentialPolicyInputSpec `json:"autoRotatePolicy,omitempty"`
}

type autoRotateCredentialPolicyInputSpec struct {
	FrequencyInDays        *int32 `json:"frequencyInDays,omitempty"`
	EnableAutoRotatePolicy bool   `json:"enableAutoRotatePolicy"`
}

type resourceCredentials struct {
	ResourceName *string          `json:"resourceName,omitempty"`
	ResourceID   *string          `json:"resourceId,omitempty"`
	ResourceType string           `json:"resourceType"`
	Credentials  []baseCredential `json:"credentials"`
}

type baseCredential struct {
	CredentialType *string `json:"credentialType,omitempty"`
	AccountType    *string `json:"accountType,omitempty"`
	Username       string  `json:"username"`
	Password       *string `json:"password,omitempty"`
}

func (m *Manager) submitAndWait(
	ctx context.Context,
	target RotationTarget,
) (CredentialsTask, error) {
	payload := credentialsUpdateSpec{
		OperationType: "ROTATE",
		Elements: []resourceCredentials{{
			ResourceName: target.ResourceName,
			ResourceID:   target.ResourceID,
			ResourceType: target.ResourceType,
			Credentials: []baseCredential{{
				CredentialType: target.CredentialType,
				AccountType:    target.AccountType,
				Username:       target.Username,
			}},
		}},
	}
	responseBody, err := m.request(
		ctx,
		"updateOrRotatePasswords",
		http.MethodPatch,
		"/v1/credentials",
		payload,
		http.StatusAccepted,
	)
	if err != nil {
		return CredentialsTask{}, err
	}
	var accepted Task
	if json.Unmarshal(responseBody, &accepted) != nil ||
		strings.TrimSpace(accepted.ID) == "" {
		return CredentialsTask{}, &ProtocolError{
			OperationID: "updateOrRotatePasswords",
			Reason:      "accepted Task is missing a usable id",
		}
	}
	return m.waitForTask(ctx, accepted.ID)
}

func (m *Manager) waitForTask(
	ctx context.Context,
	taskID string,
) (CredentialsTask, error) {
	var last CredentialsTask
	for completedPolls := 1; completedPolls <= m.maxPolls; completedPolls++ {
		if err := ctx.Err(); err != nil {
			return last, err
		}
		responseBody, err := m.request(
			ctx,
			"getCredentialsTask",
			http.MethodGet,
			"/v1/credentials/tasks/"+url.PathEscape(taskID),
			nil,
			http.StatusOK,
		)
		if err != nil {
			return last, err
		}
		if json.Unmarshal(responseBody, &last) != nil {
			return CredentialsTask{}, &ProtocolError{
				OperationID: "getCredentialsTask",
				Reason:      "response is not a CredentialsTask object",
			}
		}
		if last.ID != taskID || strings.TrimSpace(last.Status) == "" {
			return last, &ProtocolError{
				OperationID: "getCredentialsTask",
				Reason:      "task identity or status does not match the request",
			}
		}
		switch normalizedStatus(last.Status) {
		case "SUCCESSFUL":
			return last, nil
		case "FAILED", "USER_CANCELLED", "INCONSISTENT":
			return last, &TaskTerminalError{Task: last}
		case "PENDING", "IN_PROGRESS":
			if completedPolls == m.maxPolls {
				return last, &PollLimitError{
					TaskID:     taskID,
					MaxPolls:   m.maxPolls,
					LastStatus: last.Status,
				}
			}
		default:
			return last, &ProtocolError{
				OperationID: "getCredentialsTask",
				Reason:      "unrecognized credential task status",
			}
		}
		if err := m.pace(ctx, "getCredentialsTask", completedPolls); err != nil {
			return last, err
		}
		if err := ctx.Err(); err != nil {
			return last, err
		}
	}
	panic("unreachable")
}

func passwordFromTask(
	task CredentialsTask,
	username string,
) (string, error) {
	var password string
	matches := 0
	for _, subTask := range task.SubTasks {
		if subTask.Username == username {
			matches++
			password = subTask.NewPassword
		}
	}
	if matches != 1 || password == "" {
		return "", &ProtocolError{
			OperationID: "getCredentialsTask",
			Reason:      "successful task does not identify exactly one generated password",
		}
	}
	return password, nil
}

func (m *Manager) request(
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
		m.baseURL+path,
		reader,
	)
	if err != nil {
		if contextError := ctx.Err(); contextError != nil {
			return nil, contextError
		}
		return nil, &TransportError{OperationID: operationID}
	}
	request.Header.Set("Accept", "application/json")
	request.Header.Set("Authorization", "Bearer "+m.accessToken)
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}

	response, err := m.httpClient.Do(request)
	if err != nil {
		if response != nil && response.Body != nil {
			_ = response.Body.Close()
		}
		if contextError := ctx.Err(); contextError != nil {
			return nil, contextError
		}
		return nil, &TransportError{OperationID: operationID}
	}
	if response.Body == nil {
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

func normalizedStatus(value string) string {
	return strings.ReplaceAll(strings.ToUpper(strings.TrimSpace(value)), " ", "_")
}
