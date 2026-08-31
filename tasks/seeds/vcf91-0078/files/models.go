package nsxpolicy

import (
	"fmt"
	"net/http"
)

const ListAllInfraSegmentsOperation = "ListAllInfraSegments"

// Config contains the NSX Manager origin, Basic credentials, and optional
// transport. NewClient copies HTTPClient before changing redirect behavior.
type Config struct {
	BaseURL    string
	Username   string
	Password   string
	HTTPClient *http.Client
}

// ListOptions projects the caller-controlled optional query parameters
// declared by ListAllInfraSegments. Cursor is intentionally client-owned.
type ListOptions struct {
	IncludeMarkedForDelete *bool
	IncludedFields         *string
	PageSize               *int64
	SegmentType            *string
	SortAscending          *bool
	SortBy                 *string
}

// Segment is the response projection used by inventory callers.
type Segment struct {
	ID          string `json:"id"`
	DisplayName string `json:"display_name"`
	Path        string `json:"path"`
}

// ProtocolError reports a malformed success document or pagination cycle.
type ProtocolError struct {
	OperationID string
	Reason      string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("%s returned an invalid success document: %s", e.OperationID, e.Reason)
}

// APIError preserves the projected NSX error envelope without including
// server-controlled text in Error().
type APIError struct {
	OperationID  string
	StatusCode   int
	Envelope     any
	ErrorCode    *int64
	ErrorMessage string
	ModuleName   string
	Details      string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s failed with HTTP status %d", e.OperationID, e.StatusCode)
}

// TransportError retains its cause for errors.Is/errors.As while keeping the
// underlying transport text out of Error().
type TransportError struct {
	OperationID string
	Cause       error
}

func (e *TransportError) Error() string {
	return fmt.Sprintf("%s transport failed", e.OperationID)
}

func (e *TransportError) Unwrap() error {
	return e.Cause
}
