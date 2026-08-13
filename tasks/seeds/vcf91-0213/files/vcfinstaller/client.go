// Package vcfinstaller implements the focused VCF Installer bootstrap client.
package vcfinstaller

import (
	"context"
	"errors"
	"fmt"
	"net/http"
)

// ProxyConfiguration contains only writable ProxyConfiguration members.
// Pointers preserve the difference between an unset field and an explicit
// empty, zero, or false value.
type ProxyConfiguration struct {
	IsEnabled        *bool   `json:"isEnabled,omitempty"`
	Host             *string `json:"host,omitempty"`
	Port             *int32  `json:"port,omitempty"`
	TransferProtocol *string `json:"transferProtocol,omitempty"`
	Username         *string `json:"username,omitempty"`
	Password         *string `json:"password,omitempty"`
	IsAuthenticated  *bool   `json:"isAuthenticated,omitempty"`
}

// DepotAccount contains only writable DepotAccount credential members.
type DepotAccount struct {
	Username               *string `json:"username,omitempty"`
	Password               *string `json:"password,omitempty"`
	DownloadToken          *string `json:"downloadToken,omitempty"`
	DownloadActivationCode *string `json:"downloadActivationCode,omitempty"`
}

// DepotConfiguration selects online or offline depot access. IsOfflineDepot
// is required by the specification, so false is serialized when this object is
// present.
type DepotConfiguration struct {
	IsOfflineDepot bool    `json:"isOfflineDepot"`
	Hostname       *string `json:"hostname,omitempty"`
	Port           *int32  `json:"port,omitempty"`
	URL            *string `json:"url,omitempty"`
}

// DepotSettings is the writable subset of the DepotSettings request schema.
type DepotSettings struct {
	VMwareAccount      *DepotAccount       `json:"vmwareAccount,omitempty"`
	OfflineAccount     *DepotAccount       `json:"offlineAccount,omitempty"`
	DepotConfiguration *DepotConfiguration `json:"depotConfiguration,omitempty"`
}

// Outcome summarizes the three-step workflow without claiming that an HTTP
// 202 response means server-side completion.
type Outcome string

const (
	OutcomeAccepted       Outcome = "Accepted"
	OutcomeFailed         Outcome = "Failed"
	OutcomePartialFailure Outcome = "PartialFailure"
)

// StepStatus is the state of one attempted or unattempted operation.
type StepStatus string

const (
	StepAccepted StepStatus = "Accepted"
	StepFailed   StepStatus = "Failed"
	StepNotRun   StepStatus = "NotRun"
)

// StepResult reports exactly what the client learned about one operation.
type StepResult struct {
	OperationID  string
	Status       StepStatus
	HTTPStatus   int
	TaskID       string
	ErrorCode    string
	ErrorMessage string
}

// ChangeReport always contains the three contract operations in call order.
type ChangeReport struct {
	Outcome Outcome
	Steps   []StepResult
}

// APIError preserves the focused VCF Error response fields.
type APIError struct {
	OperationID        string
	StatusCode         int
	ErrorCode          string
	ErrorType          string
	Message            string
	RemediationMessage string
	ReferenceToken     string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s failed with HTTP status %d: %s: %s", e.OperationID, e.StatusCode, e.ErrorCode, e.Message)
}

// ProtocolError reports a malformed accepted response.
type ProtocolError struct {
	OperationID string
	Message     string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("%s returned an invalid response: %s", e.OperationID, e.Message)
}

// TransportError reports a request failure without exposing the underlying
// transport's potentially sensitive error text.
type TransportError struct {
	OperationID string
}

func (e *TransportError) Error() string {
	return fmt.Sprintf("%s transport failed", e.OperationID)
}

// Client calls only the operations in the focused contract.
type Client struct {
	baseURL     string
	accessToken string
	httpClient  *http.Client
}

// ErrNotImplemented is returned by the exercise stub.
var ErrNotImplemented = errors.New("vcfinstaller bootstrap client is not implemented")

// NewClient constructs a client for a VCF Installer service root.
func NewClient(baseURL, accessToken string, httpClient *http.Client) (*Client, error) {
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{baseURL: baseURL, accessToken: accessToken, httpClient: httpClient}, nil
}

// ConfigureDepotAccess applies the three contract operations in order and
// returns an exact partial-progress report on failure.
func (c *Client) ConfigureDepotAccess(ctx context.Context, proxy ProxyConfiguration, depot DepotSettings) (ChangeReport, error) {
	return ChangeReport{}, ErrNotImplemented
}
