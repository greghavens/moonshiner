// Package credrotate rotates the stored credentials of a VCF Automation cloud
// account over the focused contract recorded in docs/contract.json.
//
// Two different secrets appear in this package and they must not be confused:
//
//   - The cloud account's own credential (privateKeyId / privateKey) is the
//     payload being rotated by updateCloudAccountAsync.
//   - The caller's API session credential is the bearer token returned by
//     retrieveAuthToken. VCF Automation may revoke it at any moment, including
//     while a rotation is mid-flight.
//
// The request-shaping types below carry no JSON struct tags on purpose: the
// contract, not Go field naming, decides what goes on the wire.
package credrotate

import (
	"errors"
	"fmt"
	"net/http"
	"time"
)

// Contract operationIds, as recorded in docs/contract.json.
const (
	OperationRetrieveAuthToken       = "retrieveAuthToken"
	OperationUpdateCloudAccountAsync = "updateCloudAccountAsync"
	OperationGetRequestTracker       = "getRequestTracker"
	OperationGetCloudAccount         = "getCloudAccount"
)

// RequestTracker status values.
const (
	StatusInProgress = "INPROGRESS"
	StatusFinished   = "FINISHED"
	StatusFailed     = "FAILED"
)

// Config configures a Client.
type Config struct {
	// BaseURL is the VCF Automation origin.
	BaseURL string
	// RefreshToken is exchanged for a bearer token by retrieveAuthToken, and
	// re-exchanged whenever the current bearer token is refused.
	RefreshToken string
	// APIVersion is sent as the apiVersion query parameter on every operation.
	APIVersion string
	// HTTPClient issues every request. Required.
	HTTPClient *http.Client
	// PollInterval is the delay between getRequestTracker polls. Defaults to
	// one second when zero.
	PollInterval time.Duration
	// MaxPollAttempts bounds getRequestTracker polling. Defaults to 60.
	MaxPollAttempts int
}

// Region is one RegionSpecification. Both members are required.
type Region struct {
	Name             string
	ExternalRegionID string
}

// Tag is one Tag. Key and Value are required; ID is optional and is omitted
// from the wire body when nil.
type Tag struct {
	Key   string
	Value string
	ID    *string
}

// CertificateInfo is one CertificateInfoSpecification.
type CertificateInfo struct {
	Certificate string
}

// UpdateCloudAccountInput is the writable half of
// UpdateCloudAccountSpecification. Members typed as pointers are optional:
// a nil pointer must be absent from the serialized body, while a pointer to a
// zero value ("", false, an empty map, an empty slice) must be present and
// carry that zero value. Name, CloudAccountProperties and Regions are
// required by the contract and are therefore not pointers.
type UpdateCloudAccountInput struct {
	Name                              string
	CloudAccountProperties            map[string]string
	Regions                           []Region
	Description                       *string
	PrivateKeyID                      *string
	PrivateKey                        *string
	AssociatedCloudAccountIDs         *[]string
	AssociatedMobilityCloudAccountIDs *map[string]string
	CustomProperties                  *map[string]string
	CreateDefaultZones                *bool
	Tags                              *[]Tag
	CertificateInfo                   *CertificateInfo
}

// RequestTracker is the RequestTracker response projection.
type RequestTracker struct {
	Progress     int      `json:"progress"`
	Message      string   `json:"message,omitempty"`
	Status       string   `json:"status"`
	Resources    []string `json:"resources,omitempty"`
	Name         string   `json:"name,omitempty"`
	ID           string   `json:"id"`
	SelfLink     string   `json:"selfLink"`
	DeploymentID string   `json:"deploymentId,omitempty"`
}

// CloudAccount is the getCloudAccount response projection.
type CloudAccount struct {
	ID                     string            `json:"id"`
	Name                   string            `json:"name,omitempty"`
	Description            string            `json:"description,omitempty"`
	CloudAccountType       string            `json:"cloudAccountType"`
	CloudAccountProperties map[string]string `json:"cloudAccountProperties"`
	CustomProperties       map[string]string `json:"customProperties,omitempty"`
	Healthy                bool              `json:"healthy,omitempty"`
	InMaintenanceMode      bool              `json:"inMaintenanceMode,omitempty"`
}

// ServiceErrorResponse is the contract's error payload.
type ServiceErrorResponse struct {
	Message    string `json:"message"`
	MessageID  string `json:"messageId"`
	StatusCode int    `json:"statusCode"`
}

// RotationResult reports the outcome of a completed rotation.
type RotationResult struct {
	// Tracker is the terminal getRequestTracker state.
	Tracker RequestTracker
	// Account is the cloud account as read back after the rotation finished.
	Account CloudAccount
	// Reauthentications counts how many extra retrieveAuthToken exchanges the
	// rotation needed because the bearer token was revoked mid-flight.
	Reauthentications int
}

// ErrInvalidInput marks a request rejected before any HTTP call is made.
var ErrInvalidInput = errors.New("invalid input")

// ErrorKind classifies failures without exposing credentials.
type ErrorKind string

const (
	KindHTTP      ErrorKind = "http"
	KindTransport ErrorKind = "transport"
	KindProtocol  ErrorKind = "protocol"
)

// OperationError reports which contract operation failed.
type OperationError struct {
	OperationID string
	Kind        ErrorKind
	StatusCode  int
	Cause       error
}

func (e *OperationError) Error() string {
	switch e.Kind {
	case KindHTTP:
		return fmt.Sprintf("%s failed with HTTP %d", e.OperationID, e.StatusCode)
	case KindTransport:
		return fmt.Sprintf("%s transport failed", e.OperationID)
	default:
		return fmt.Sprintf("%s returned an invalid response", e.OperationID)
	}
}

// Unwrap keeps context cancellation and deadline errors discoverable.
func (e *OperationError) Unwrap() error { return e.Cause }

// TrackerError reports a rotation that the service itself refused, either by
// reaching FAILED or by never leaving INPROGRESS within the poll budget.
type TrackerError struct {
	RequestID string
	Status    string
	Message   string
}

func (e *TrackerError) Error() string {
	if e.Message == "" {
		return fmt.Sprintf("request %s ended in status %s", e.RequestID, e.Status)
	}
	return fmt.Sprintf("request %s ended in status %s: %s", e.RequestID, e.Status, e.Message)
}
