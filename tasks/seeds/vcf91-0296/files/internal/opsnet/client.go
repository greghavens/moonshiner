// Package opsnet is a client for the VCF Operations for Networks API in VMware
// Cloud Foundation 9.1 (the successor to vRealize Network Insight).
//
// The wire contract this package must honour is docs/contract.json, which is
// derived from the product's OpenAPI document; docs/official_sources.json
// records where that document came from. Only four operations are in scope:
//
//	create                POST   /api/ni/auth/token
//	delete                DELETE /api/ni/auth/token
//	listApplications      GET    /api/ni/groups/applications
//	getApplicationById    GET    /api/ni/groups/applications/{id}
package opsnet

import (
	"context"
	"errors"
	"net/http"
	"time"
)

// SpecBasePath is the servers[0].url value from the specification. It must be
// prefixed to every operation path.
const SpecBasePath = "/api/ni"

// AuthHeaderPrefix is the prefix the ApiKeyAuth security scheme requires in the
// Authorization header ("API Key - NetworkInsight {token}").
const AuthHeaderPrefix = "NetworkInsight "

// Domain types accepted by the Domain schema's domain_type enum.
const (
	DomainLocal = "LOCAL"
	DomainLDAP  = "LDAP"
)

// ErrNotImplemented is returned by the unimplemented parts of this package.
var ErrNotImplemented = errors.New("opsnet: not implemented")

// Credentials are the values sent in the UserCredential request body of
// operationId "create".
type Credentials struct {
	// Username maps to UserCredential.username.
	Username string
	// Password maps to UserCredential.password.
	Password string
	// DomainType maps to Domain.domain_type. When empty the whole domain object
	// must be absent from the request body.
	DomainType string
	// DomainValue maps to Domain.value. The specification notes it is "not
	// required for LOCAL domain"; when empty it must be absent from the domain
	// object.
	DomainValue string
}

// Config configures a Client.
type Config struct {
	// BaseURL is the appliance origin, for example "https://vcfops.example.net".
	// It carries no path component; the client appends SpecBasePath itself.
	BaseURL string

	// Credentials are used by operationId "create", both for the initial login
	// and for every refresh.
	Credentials Credentials

	// HTTPClient is used for every request. nil means http.DefaultClient.
	HTTPClient *http.Client

	// Now supplies the current time. nil means time.Now.
	Now func() time.Time

	// RefreshSkew makes the client refresh a token before it actually expires:
	// before issuing an authenticated request, if the token expires in
	// RefreshSkew or less, refresh it first. Zero disables proactive refresh.
	RefreshSkew time.Duration

	// PageSize is the value for the listApplications "size" query parameter.
	// Zero means the parameter must be omitted so the server default applies.
	PageSize int

	// DetailConcurrency is how many getApplicationById requests may be in
	// flight at once. Zero or one means fully sequential.
	DetailConcurrency int

	// FetchMemberCounts sets fetch_member_counts=true on getApplicationById.
	// When false the parameter must be omitted.
	FetchMemberCounts bool

	// FetchUpdateStatus sets fetch_update_status=true on getApplicationById.
	// When false the parameter must be omitted.
	FetchUpdateStatus bool
}

// Application is the subset of the Application schema this client surfaces.
type Application struct {
	EntityID   string
	Name       string
	EntityType string
	// TierCount and MemberCount are only populated when
	// Config.FetchMemberCounts was set.
	TierCount   int
	MemberCount int
	// UpdateStatus is only populated when Config.FetchUpdateStatus was set.
	UpdateStatus string
}

// Inventory is the result of a full collection run.
type Inventory struct {
	// Applications are ordered by their position in the paginated listing, not
	// by the order detail responses happened to arrive.
	Applications []Application
	// TotalCount is PagedListResponse.total_count from the last page.
	TotalCount int
	// Pages is the number of listApplications responses that succeeded.
	Pages int
}

// Client talks to one VCF Operations for Networks appliance.
type Client struct {
	// TODO: implement.
	cfg Config
}

// New validates cfg and returns a Client. It performs no I/O: no token is
// created until the first operation that needs one.
func New(cfg Config) (*Client, error) {
	return nil, ErrNotImplemented
}

// CollectInventory performs a full application inventory collection:
//
//  1. obtain an auth token via operationId "create";
//  2. walk every page of operationId "listApplications", following
//     PagedListResponse.cursor until the response omits it;
//  3. fetch each listed application via operationId "getApplicationById",
//     using up to Config.DetailConcurrency concurrent requests.
//
// The auth token may expire at any point during the run. When that happens the
// client must recover without losing work: the failed request is retried with a
// fresh token and everything already collected is kept. Pagination resumes from
// the cursor of the page that failed - it does not restart from the first page,
// and no page that already succeeded is fetched again.
//
// CollectInventory does not revoke the token; call Close for that.
func (c *Client) CollectInventory(ctx context.Context) (*Inventory, error) {
	return nil, ErrNotImplemented
}

// Close revokes the client's current auth token via operationId "delete". The
// specification advises deleting tokens after use because a user may hold only
// 100 valid tokens. Close is a no-op returning nil when the client holds no
// token, and it must revoke the token the client currently holds rather than
// one that has already been replaced.
func (c *Client) Close(ctx context.Context) error {
	return ErrNotImplemented
}

// TokenCreates reports how many times operationId "create" returned a token to
// this client over its lifetime, including the initial login. It is safe to
// call while CollectInventory is running.
func (c *Client) TokenCreates() int {
	return 0
}
