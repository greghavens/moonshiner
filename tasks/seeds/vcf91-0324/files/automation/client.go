// Package automation is a client for the subset of the VCF Automation 9.1 API
// described by docs/contract.json.
//
// The service issues short-lived access tokens. A long run will outlive its
// token, so the client must notice the expiry, exchange its long-lived API
// token for a fresh access token, and carry on from where it was — without
// dropping results it already has and without redoing work it already did.
//
// Every exported symbol in this file is part of the package's contract with
// its callers; the signatures are fixed. The bodies are yours to write.
package automation

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"

	"vcfauto/contract"
)

// errNotImplemented is returned by the unimplemented stubs below.
var errNotImplemented = errors.New("automation: not implemented")

// Config configures a Client.
type Config struct {
	// BaseURL is the scheme and host of the VCF Automation appliance, with
	// no trailing slash, e.g. "https://automation.example.com".
	BaseURL string

	// Tenant is the organization name used by the token operation.
	Tenant string

	// APIToken is the long-lived, organization-scoped API token. It is
	// exchanged for a short-lived access token, and re-exchanged whenever
	// that access token stops being accepted.
	APIToken string

	// Contract is the loaded docs/contract.json. Requests are built from
	// it: paths, query parameter names and body field names all come from
	// the contract rather than from literals in this package.
	Contract *contract.Contract

	// HTTPClient is used for every request. Defaults to a client with a
	// sane timeout when nil.
	HTTPClient *http.Client

	// Concurrency bounds how many requests the Collect* helpers keep in
	// flight at once. Defaults to 4 when zero.
	Concurrency int
}

// Client talks to one VCF Automation appliance.
//
// A Client is safe for concurrent use. When several goroutines discover at the
// same moment that the access token has expired, they must between them cause
// exactly one token exchange, not one each.
type Client struct {
	cfg Config
	// Add whatever state you need. Keep it race-free: the test suite runs
	// under -race with concurrent callers.
}

// New validates cfg and returns a Client. It does not perform any I/O; the
// first access token is fetched lazily, when it is first needed.
func New(cfg Config) (*Client, error) {
	return nil, errNotImplemented
}

// APIError is a non-2xx response from the service.
type APIError struct {
	// Operation is the contract operation ID that produced the request.
	Operation  string
	Method     string
	Path       string
	StatusCode int
	Body       string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("automation: %s %s %s: unexpected status %d: %s",
		e.Operation, e.Method, e.Path, e.StatusCode, e.Body)
}

// Deployment is a deployment as returned by the deployments operations. Only
// the fields the client needs are decoded; Raw keeps the object as served.
type Deployment struct {
	ID        string
	Name      string
	Status    string
	ProjectID string
	CreatedAt string
	Raw       json.RawMessage
}

// DeploymentPage is one page of deployments.
type DeploymentPage struct {
	Content          []Deployment
	Number           int
	Size             int
	NumberOfElements int
	TotalElements    int
	TotalPages       int
	First            bool
	Last             bool
	Empty            bool
}

// CatalogItem is a catalog item as returned by the catalog operations.
type CatalogItem struct {
	ID          string
	Name        string
	Description string
	Raw         json.RawMessage
}

// CatalogItemPage is one page of catalog items.
type CatalogItemPage struct {
	Content          []CatalogItem
	Number           int
	Size             int
	NumberOfElements int
	TotalElements    int
	TotalPages       int
	First            bool
	Last             bool
	Empty            bool
}

// CatalogItemRequestResult is one entry of the catalog request response.
type CatalogItemRequestResult struct {
	DeploymentID   string
	DeploymentName string
}

// ListDeploymentsOptions carries the optional query parameters of the
// deployments.list operation. Every field is optional: an unset field must not
// appear in the query string at all.
type ListDeploymentsOptions struct {
	Page     Opt[int]
	Size     Opt[int]
	Sort     Opt[string]
	Search   Opt[string]
	Name     Opt[string]
	Projects Opt[[]string]
	Status   Opt[[]string]
	Expand   Opt[[]string]
	Deleted  Opt[bool]
}

// GetDeploymentOptions carries the optional query parameters of the
// deployments.get operation.
type GetDeploymentOptions struct {
	Expand  Opt[[]string]
	Deleted Opt[bool]
}

// ListCatalogItemsOptions carries the optional query parameters of the
// catalog.items.list operation.
type ListCatalogItemsOptions struct {
	Page     Opt[int]
	Size     Opt[int]
	Search   Opt[string]
	Projects Opt[[]string]
	Types    Opt[[]string]
}

// CatalogItemRequest is the body of the catalog.items.request operation. The
// reference documents every field as optional, so an unset field must be
// absent from the JSON object rather than present and empty.
type CatalogItemRequest struct {
	DeploymentName   Opt[string]
	ProjectID        Opt[string]
	Version          Opt[string]
	Reason           Opt[string]
	BulkRequestCount Opt[int]
	Inputs           Opt[map[string]any]
}

// ListDeployments fetches one page of deployments.
func (c *Client) ListDeployments(ctx context.Context, opts ListDeploymentsOptions) (*DeploymentPage, error) {
	return nil, errNotImplemented
}

// GetDeployment fetches one deployment by ID.
func (c *Client) GetDeployment(ctx context.Context, deploymentID string, opts GetDeploymentOptions) (*Deployment, error) {
	return nil, errNotImplemented
}

// ListCatalogItems fetches one page of catalog items.
func (c *Client) ListCatalogItems(ctx context.Context, opts ListCatalogItemsOptions) (*CatalogItemPage, error) {
	return nil, errNotImplemented
}

// RequestCatalogItem requests a new deployment from a catalog item.
func (c *Client) RequestCatalogItem(ctx context.Context, catalogItemID string, req CatalogItemRequest) ([]CatalogItemRequestResult, error) {
	return nil, errNotImplemented
}

// CollectDeployments walks every page of deployments and returns them in page
// order.
//
// opts supplies the starting page and page size; the walk continues until the
// service reports the last page. If the access token expires part way through
// the walk, the walk must resume at the page it was on: a page that has
// already been read successfully must not be read a second time, and the
// deployments already collected must not be discarded.
func (c *Client) CollectDeployments(ctx context.Context, opts ListDeploymentsOptions) ([]Deployment, error) {
	return nil, errNotImplemented
}

// CollectDeploymentDetails fetches each of the given deployments by ID, with
// at most Config.Concurrency requests in flight, and returns them in the same
// order as ids.
//
// If the access token expires while these requests are in flight, every one of
// them must still succeed, and the concurrent callers must between them cause
// exactly one token exchange.
func (c *Client) CollectDeploymentDetails(ctx context.Context, ids []string, opts GetDeploymentOptions) ([]Deployment, error) {
	return nil, errNotImplemented
}
