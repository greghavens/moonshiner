// Package customgroup is a client for the custom group operations of the
// VMware Cloud Foundation Operations API.
//
// The HTTP surface it targets is described by docs/contract.json, which is
// derived from the product's OpenAPI specification. Only the operationIds the
// contract names may be called.
package customgroup

import (
	"context"
	"errors"
	"net/http"
)

// BasePath is the server base path the contract declares for the suite-api.
// Operation paths from the contract are appended to it.
const BasePath = "/suite-api"

// ErrNotImplemented is returned by the unfinished parts of this package.
var ErrNotImplemented = errors.New("customgroup: not implemented")

// ResourceKey is the composite key that uniquely identifies a resource. The
// appliance treats the triple of name, adapter kind and resource kind as the
// identity of a custom group: two groups cannot share it.
//
// TODO: give these fields the JSON encoding the contract's resource-key schema
// requires.
type ResourceKey struct {
	// Name is the display name of the custom group.
	Name string
	// AdapterKindKey is the adapter kind the group belongs to.
	AdapterKindKey string
	// ResourceKindKey is the resource kind the group belongs to.
	ResourceKindKey string
}

// MembershipDefinition describes which resources belong to a custom group.
//
// The contract marks this property required on custom-group, so it is always
// part of a create request even when no members are named.
//
// TODO: give these fields the JSON encoding the contract's
// custom-group-membership schema requires.
type MembershipDefinition struct {
	// IncludedResources holds resource identifiers to place in the group.
	IncludedResources []string
	// ExcludedResources holds resource identifiers to keep out of the group.
	ExcludedResources []string
}

// CustomGroup mirrors the contract's custom-group schema. The same type is used
// for create requests and for decoding responses, so properties the server
// assigns must not appear in an outbound request.
//
// TODO: give these fields the JSON encoding the contract's custom-group schema
// requires.
type CustomGroup struct {
	// ID is assigned by the appliance. It is empty on a create request.
	ID string
	// ResourceKey identifies the group. Required.
	ResourceKey ResourceKey
	// AutoResolveMembership controls whether membership is recomputed
	// automatically. It is tri-state: nil leaves the property out of the
	// request so the appliance default applies, while a non-nil pointer sends
	// the value explicitly, including false.
	AutoResolveMembership *bool
	// MembershipDefinition is required by the contract.
	MembershipDefinition MembershipDefinition
	// Policy is the identifier of the policy applied to the group. It is
	// returned by the appliance and is empty on a create request.
	Policy string
}

// ListOptions holds the optional query parameters of getCustomGroups. A zero
// ListOptions must produce a request with no query string at all: the contract
// marks both parameters optional and gives includePolicy the default false.
type ListOptions struct {
	// GroupIDs restricts the result to these group identifiers.
	GroupIDs []string
	// IncludePolicy asks the appliance to return each group's policy.
	IncludePolicy bool
}

// APIError reports a non-success HTTP response from the appliance.
type APIError struct {
	// OperationID is the contract operation that was called.
	OperationID string
	// StatusCode is the HTTP status the appliance returned.
	StatusCode int
	// Message is the human readable detail, when the body carried one.
	Message string
	// Body is the raw response body.
	Body []byte
}

func (e *APIError) Error() string {
	return "customgroup: " + e.OperationID + ": unexpected status " +
		http.StatusText(e.StatusCode) + ": " + e.Message
}

// Client calls the custom group operations of a VCF Operations appliance.
type Client struct {
	baseURL string
	auth    string
	http    *http.Client
}

// NewClient returns a Client for the appliance at baseURL, for example
// https://vcfops.example.com. The authorization argument is sent verbatim as
// the value of the header named by the contract's security scheme. A nil
// httpClient selects http.DefaultClient.
func NewClient(baseURL, authorization string, httpClient *http.Client) (*Client, error) {
	return nil, ErrNotImplemented
}

// ListCustomGroups calls getCustomGroups and returns the custom groups the
// appliance holds.
func (c *Client) ListCustomGroups(ctx context.Context, opts ListOptions) ([]CustomGroup, error) {
	return nil, ErrNotImplemented
}

// CreateCustomGroup calls createCustomGroup and returns the created group as
// the appliance recorded it.
func (c *Client) CreateCustomGroup(ctx context.Context, group CustomGroup) (CustomGroup, error) {
	return CustomGroup{}, ErrNotImplemented
}

// EnsureResult reports the outcome of EnsureCustomGroup.
type EnsureResult struct {
	// Group is the custom group that exists on the appliance afterwards.
	Group CustomGroup
	// Created is true only when this call was the one that created the group.
	Created bool
}

// EnsureCustomGroup makes the desired custom group exist on the appliance and
// is safe to call repeatedly: however many times it runs, at most one group
// with the desired resource key exists afterwards.
func (c *Client) EnsureCustomGroup(ctx context.Context, desired CustomGroup) (EnsureResult, error) {
	return EnsureResult{}, ErrNotImplemented
}
