package nsxpolicy

import (
	"fmt"
	"net/http"
)

const (
	ReadInfraSegmentOperation  = "ReadInfraSegment"
	PatchInfraSegmentOperation = "PatchInfraSegment"
)

// Config contains the NSX Policy origin, Basic-auth credentials, and optional
// transport. NewClient copies HTTPClient before changing redirect behavior.
type Config struct {
	BaseURL    string
	Username   string
	Password   string
	HTTPClient *http.Client
}

// EnableRequest contains the values that the read precheck must confirm and
// the sole optional property that may be included in the PATCH.
type EnableRequest struct {
	ExpectedRevision         int32
	ExpectedConnectivityPath string
	Description              *string
}

// Result describes the confirmed transition after a successful PATCH.
type Result struct {
	SegmentID           string
	Revision            int32
	PreviousAdminState  string
	AdminState          string
	Changed             bool
	ReadOperationID     string
	MutationOperationID string
}

// PrecheckError reports a valid precheck document that did not match the gate.
type PrecheckError struct {
	SegmentID string
	Reason    string
}

func (e *PrecheckError) Error() string {
	return fmt.Sprintf("%s precheck rejected segment %q: %s", ReadInfraSegmentOperation, e.SegmentID, e.Reason)
}

// ProtocolError reports a malformed success document.
type ProtocolError struct {
	OperationID string
	Reason      string
}

func (e *ProtocolError) Error() string {
	return fmt.Sprintf("%s returned an invalid success document: %s", e.OperationID, e.Reason)
}

// APIError preserves the projected NSX error envelope without including it in
// Error(), so server-controlled text is not accidentally logged.
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
