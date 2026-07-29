// Package nsxpolicy implements the focused VMware Cloud Foundation 9.1
// NSX Policy contract described in docs/contract.json.
package nsxpolicy

import (
	"net/http"
)

const operationID = "CreateOrPatchIpAddressBlock"

// Config configures a Client without performing an API request.
type Config struct {
	BaseURL    string
	Username   string
	Password   string
	HTTPClient *http.Client
}

// IPAddressBlock is the request projection for CreateOrPatchIpAddressBlock.
// Pointer fields distinguish an unset optional member from an explicit value.
type IPAddressBlock struct {
	DisplayName     string   `json:"display_name"`
	CIDRs           []string `json:"cidrs"`
	Description     *string  `json:"description,omitempty"`
	SubnetExclusive *bool    `json:"subnet_exclusive,omitempty"`
}

// Result identifies a successfully applied operation.
type Result struct {
	OperationID string
	IPBlockID   string
	Attempts    int
}

// APIError represents a non-successful NSX Policy response.
type APIError struct {
	StatusCode   int
	ErrorCode    int64
	ErrorMessage string
	ModuleName   string
	Details      string
}

func (e *APIError) Error() string {
	return "NSX Policy request failed"
}

// TransportError hides transport details while preserving errors.Is support.
type TransportError struct {
	Err error
}

func (e *TransportError) Error() string {
	return "NSX Policy transport failed"
}

func (e *TransportError) Unwrap() error {
	return e.Err
}

// ValidationError reports invalid local input.
type ValidationError struct {
	Field string
}

func (e *ValidationError) Error() string {
	return "invalid NSX Policy " + e.Field
}

// Client invokes the focused NSX Policy contract.
type Client struct {
	baseURL    string
	username   string
	password   string
	httpClient *http.Client
}
