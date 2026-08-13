// Package nsxpolicy implements the focused NSX Policy bulk-tag workflow used
// by this exercise.
package nsxpolicy

import (
	"fmt"
	"net/http"
	"time"
)

const (
	OperationTagBulkUpdate             = "TagBulkUpdate"
	OperationGetTagBulkOperationStatus = "GetTagBulkOperationStatus"

	StatusPending = "Pending"
	StatusRunning = "Running"
	StatusSuccess = "Success"
	StatusError   = "Error"

	ActionApply  = "apply"
	ActionRemove = "remove"
)

// Config controls an NSX Policy client. MaxPolls counts status GETs, not the
// initial mutation. PollInterval may be zero.
type Config struct {
	BaseURL      string
	Username     string
	Password     string
	HTTPClient   *http.Client
	PollInterval time.Duration
	MaxPolls     int
}

// Tag is the writable projection of the specification's Tag definition. A nil
// Scope is unset and must be omitted.
type Tag struct {
	Scope *string `json:"scope,omitempty"`
	Tag   string  `json:"tag"`
}

// ResourceInfo identifies resources affected by a bulk tag operation.
type ResourceInfo struct {
	ResourceType string   `json:"resource_type"`
	ResourceIDs  []string `json:"resource_ids"`
}

// BulkTagRequest is the writable projection of TagBulkOperation. Nil slices
// are unset; non-nil empty slices are invalid.
type BulkTagRequest struct {
	Tag        Tag            `json:"tag"`
	ApplyTo    []ResourceInfo `json:"apply_to,omitempty"`
	RemoveFrom []ResourceInfo `json:"remove_from,omitempty"`
}

// ResourceTagStatus reports the result for one resource.
type ResourceTagStatus struct {
	ResourceID string `json:"resource_id"`
	TagStatus  string `json:"tag_status"`
	Details    string `json:"details,omitempty"`
}

// ResourceTypeTagStatus groups per-resource outcomes by resource type.
type ResourceTypeTagStatus struct {
	ResourceType      string              `json:"resource_type"`
	ResourceTagStatus []ResourceTagStatus `json:"resource_tag_status,omitempty"`
}

// TagBulkOperationStatus is returned by GetTagBulkOperationStatus.
type TagBulkOperationStatus struct {
	Path       string                  `json:"path"`
	Status     string                  `json:"status"`
	Tag        Tag                     `json:"tag"`
	ApplyTo    []ResourceTypeTagStatus `json:"apply_to,omitempty"`
	RemoveFrom []ResourceTypeTagStatus `json:"remove_from,omitempty"`
}

// Outcome is a flattened per-resource terminal result.
type Outcome struct {
	Action       string
	ResourceType string
	ResourceID   string
	TagStatus    string
	Details      string
}

// Result is returned only after the successful terminal status is observed.
type Result struct {
	OperationID string
	Path        string
	Polls       int
	Outcomes    []Outcome
}

// APIError represents a non-200 response from a named contract operation.
type APIError struct {
	OperationID  string
	StatusCode   int
	Envelope     map[string]any
	ErrorCode    *int64
	ErrorMessage string
	ModuleName   string
	Details      string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("nsxpolicy: %s returned HTTP %d", e.OperationID, e.StatusCode)
}

// ProtocolError reports a malformed successful response or unknown status.
type ProtocolError struct {
	OperationID string
	Reason      string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("nsxpolicy: invalid %s response: %s", e.OperationID, e.Reason)
}

// TransportError keeps its cause discoverable without exposing transport text.
type TransportError struct {
	OperationID string
	Cause       error
}

func (e *TransportError) Error() string {
	return fmt.Sprintf("nsxpolicy: %s transport failed", e.OperationID)
}

func (e *TransportError) Unwrap() error { return e.Cause }

// OperationFailedError retains the terminal Error document and sorted outcomes.
type OperationFailedError struct {
	OperationID string
	Polls       int
	Final       TagBulkOperationStatus
	Outcomes    []Outcome
}

func (e *OperationFailedError) Error() string {
	return fmt.Sprintf("nsxpolicy: bulk tag operation %q reached Error after %d polls", e.OperationID, e.Polls)
}

// PollTimeoutError retains the final nonterminal document observed.
type PollTimeoutError struct {
	OperationID string
	MaxPolls    int
	Last        TagBulkOperationStatus
}

func (e *PollTimeoutError) Error() string {
	return fmt.Sprintf("nsxpolicy: bulk tag operation %q did not finish within %d polls", e.OperationID, e.MaxPolls)
}
