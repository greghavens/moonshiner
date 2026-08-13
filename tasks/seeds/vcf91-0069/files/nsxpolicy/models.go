// Package nsxpolicy implements the contract-pinned NSX Policy bulk-tag
// workflow used by this module.
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
)

// Config controls an NSX Policy client. MaxPolls counts status GETs, not the
// initial PUT. PollInterval may be zero.
type Config struct {
	BaseURL      string
	Username     string
	Password     string
	HTTPClient   *http.Client
	PollInterval time.Duration
	MaxPolls     int
}

// Tag is the focused projection of the specification's Tag definition. A nil
// Scope is unset and must be omitted from JSON.
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
// are unset and must be omitted. Non-nil empty slices are rejected locally.
type BulkTagRequest struct {
	Tag        Tag            `json:"tag"`
	ApplyTo    []ResourceInfo `json:"apply_to,omitempty"`
	RemoveFrom []ResourceInfo `json:"remove_from,omitempty"`
}

// ResourceTagStatus reports the terminal outcome for one resource.
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

// TagBulkOperationStatus is the status document returned by
// GetTagBulkOperationStatus.
type TagBulkOperationStatus struct {
	Path       string                  `json:"path"`
	Status     string                  `json:"status"`
	Tag        Tag                     `json:"tag"`
	ApplyTo    []ResourceTypeTagStatus `json:"apply_to,omitempty"`
	RemoveFrom []ResourceTypeTagStatus `json:"remove_from,omitempty"`
}

// Result is returned only after a successful terminal status GET.
type Result struct {
	OperationID string
	Path        string
	Status      string
	Polls       int
	Terminal    TagBulkOperationStatus
}

// APIError represents a non-200 response from a named contract operation.
type APIError struct {
	OperationID  string
	StatusCode   int
	ErrorCode    int64
	ErrorMessage string
	ModuleName   string
	Details      string
	Envelope     map[string]any
}

func (e *APIError) Error() string {
	return fmt.Sprintf("nsxpolicy: %s returned HTTP %d", e.OperationID, e.StatusCode)
}

// ProtocolError represents a malformed or unknown successful response.
type ProtocolError struct {
	OperationID string
	Reason      string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("nsxpolicy: invalid %s response: %s", e.OperationID, e.Reason)
}

// OperationFailedError retains the terminal Error status document.
type OperationFailedError struct {
	OperationID string
	Polls       int
	Final       TagBulkOperationStatus
}

func (e *OperationFailedError) Error() string {
	return fmt.Sprintf("nsxpolicy: bulk tag operation %q reached Error after %d polls", e.OperationID, e.Polls)
}

// PollTimeoutError retains the last valid nonterminal status document.
type PollTimeoutError struct {
	OperationID string
	MaxPolls    int
	Last        TagBulkOperationStatus
}

func (e *PollTimeoutError) Error() string {
	return fmt.Sprintf("nsxpolicy: bulk tag operation %q did not finish within %d polls", e.OperationID, e.MaxPolls)
}
