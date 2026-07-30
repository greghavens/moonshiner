package attestdiag

import (
	"context"
	"fmt"
	"net/http"
	"time"
)

const (
	OperationListTPMs       = "Vcenter.TrustedInfrastructure.Hosts.Hardware.Tpm_list"
	OperationGetTPMEventLog = "Vcenter.TrustedInfrastructure.Hosts.Hardware.Tpm.EventLog_get"
	OperationCreateBundle   = "Appliance.SupportBundle_create$Task"

	DiagnosisTPMInactive       = "TPM_INACTIVE"
	DiagnosisEventLogTruncated = "TPM_EVENT_LOG_TRUNCATED"
	DiagnosisUnresolvedReview  = "UNRESOLVED_REVIEW_EVENT_LOG"
	SummaryTPMInactive         = "The selected TPM is reported inactive by vCenter."
	SummaryEventLogTruncated   = "The TPM event log is truncated; review the collected evidence and support bundle."
	SummaryUnresolvedReview    = "The collected fields do not establish a root cause; review the TPM event log and support bundle."
)

// Config describes one authenticated vCenter Automation API client.
type Config struct {
	BaseURL    string
	SessionID  string
	Timeout    time.Duration
	HTTPClient *http.Client
}

// TPMListOptions is the focused projection of the list operation's optional
// query parameters. Nil means absent on the wire.
type TPMListOptions struct {
	Active        *bool
	MajorVersions []int64
}

// BundleOptions is the focused projection of optional support-bundle members.
// Nil means absent from JSON.
type BundleOptions struct {
	Components map[string][]string
	Partition  *string
}

// TPMSummary is the complete focused list-item projection.
type TPMSummary struct {
	TPM          string `json:"tpm"`
	MajorVersion int64  `json:"major_version"`
	MinorVersion int64  `json:"minor_version"`
	Active       bool   `json:"active"`
}

// PCRBank is one measured-boot PCR bank returned with an event log.
type PCRBank struct {
	Algorithm string            `json:"algorithm"`
	PCRs      map[string]string `json:"pcrs"`
}

// TPMEventLog is the complete focused event-log projection. Data is optional
// in future versions of the specification, so nil preserves its absence.
type TPMEventLog struct {
	Type      string    `json:"type"`
	Data      *string   `json:"data,omitempty"`
	Truncated bool      `json:"truncated"`
	Banks     []PCRBank `json:"banks"`
}

// Diagnosis deliberately states only what the collected fields establish.
type Diagnosis struct {
	Code    string `json:"code"`
	Summary string `json:"summary"`
}

// DiagnosisReport preserves all evidence and the support-bundle task handle.
type DiagnosisReport struct {
	Host              string      `json:"host"`
	TPM               TPMSummary  `json:"tpm"`
	EventLog          TPMEventLog `json:"event_log"`
	SupportBundleTask string      `json:"support_bundle_task"`
	Diagnosis         Diagnosis   `json:"diagnosis"`
}

// Client is safe for concurrent calls after construction.
type Client struct {
	baseURL    string
	sessionID  string
	httpClient *http.Client
}

// ValidationError reports a caller input error without including input values.
type ValidationError struct {
	Field string
}

func (e *ValidationError) Error() string {
	if e == nil {
		return "attestdiag validation failed"
	}
	return fmt.Sprintf("attestdiag validation failed: %s", e.Field)
}

// APIError represents a non-contract HTTP status. It never retains a response
// body or credential.
type APIError struct {
	OperationID string
	StatusCode  int
}

func (e *APIError) Error() string {
	if e == nil {
		return "vCenter API request failed"
	}
	return fmt.Sprintf("%s failed with HTTP %d", e.OperationID, e.StatusCode)
}

// ProtocolError represents a successful response that violates the focused
// contract.
type ProtocolError struct {
	OperationID string
	Reason      string
}

func (e *ProtocolError) Error() string {
	if e == nil {
		return "vCenter API response violated the contract"
	}
	return fmt.Sprintf("%s returned an invalid response: %s", e.OperationID, e.Reason)
}

// TransportError redacts the underlying transport text while retaining
// cancellation/deadline identity through Unwrap.
type TransportError struct {
	OperationID string
	Err         error
}

func (e *TransportError) Error() string {
	if e == nil {
		return "vCenter API transport failed"
	}
	return fmt.Sprintf("%s transport failed", e.OperationID)
}

func (e *TransportError) Unwrap() error {
	if e == nil {
		return nil
	}
	if e.Err == context.Canceled || e.Err == context.DeadlineExceeded {
		return e.Err
	}
	return nil
}
