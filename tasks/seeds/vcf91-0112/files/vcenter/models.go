// Package vcenter implements the focused VMware Cloud Foundation 9.1
// vSphere Automation contract described in docs/contract.json.
package vcenter

import (
	"net/http"
)

const operationID = "Content.LocalLibrary_create"

// StorageBackingType is a Content.Library.StorageBacking.Type value.
type StorageBackingType string

const (
	StorageBackingDatastore StorageBackingType = "DATASTORE"
	StorageBackingOther     StorageBackingType = "OTHER"
)

// Config configures a Client without performing an API request.
type Config struct {
	BaseURL      string
	SessionToken string
	HTTPClient   *http.Client
}

// StorageBacking is the create projection for one library storage location.
// Pointer fields distinguish an unset member from an explicit value.
type StorageBacking struct {
	Type        StorageBackingType `json:"type"`
	DatastoreID *string            `json:"datastore_id,omitempty"`
	StorageURI  *string            `json:"storage_uri,omitempty"`
}

// LocalLibrarySpec is the focused create projection of Content.LibraryModel.
type LocalLibrarySpec struct {
	Name            string           `json:"name"`
	StorageBackings []StorageBacking `json:"storage_backings"`
	Description     *string          `json:"description,omitempty"`
}

// CreateResult identifies a successful idempotent creation.
type CreateResult struct {
	OperationID string
	LibraryID   string
	ClientToken string
	Attempts    int
}

// ValidationError reports an invalid local input field.
type ValidationError struct {
	Field string
}

func (e *ValidationError) Error() string {
	return "invalid vCenter " + e.Field
}

// TransportError hides transport details and preserves errors.Is behavior.
type TransportError struct {
	OperationID string
	Attempts    int
	Err         error
}

func (e *TransportError) Error() string {
	return "vCenter transport failed"
}

func (e *TransportError) Unwrap() error {
	return e.Err
}

// APIError represents a non-201 vCenter response.
type APIError struct {
	OperationID string
	StatusCode  int
	Attempts    int
}

func (e *APIError) Error() string {
	return "vCenter request failed"
}

// ProtocolError represents a 201 response that violates the focused contract.
type ProtocolError struct {
	OperationID string
	Attempts    int
}

func (e *ProtocolError) Error() string {
	return "vCenter response violated the contract"
}

// Client invokes the focused local Content Library contract.
type Client struct {
	baseURL      string
	sessionToken string
	httpClient   *http.Client
}
