// Package incidents reads Guided Network Troubleshooting incidents from
// VCF Operations for Networks 9.0.
//
// The focused wire contract is in docs/contract.json. Only two operations are
// in scope:
//
//	create                         POST /api/ni/auth/token
//	listTroubleshootingIncidents  GET  /api/ni/gnt/troubleshoot/incidents
package incidents

import (
	"context"
	"net/http"
	"sync"
)

// SpecBasePath is servers[0].url in the OpenAPI specification.
const SpecBasePath = "/api/ni"

// AuthHeaderPrefix is the value prefix described by ApiKeyAuth.
const AuthHeaderPrefix = "NetworkInsight "

// DomainType values from the Domain.domain_type enum.
const (
	DomainLDAP  = "LDAP"
	DomainLocal = "LOCAL"
)

// Credentials map to the UserCredential and Domain schemas.
type Credentials struct {
	Username    string
	Password    string
	DomainType  string
	DomainValue string
}

// Config configures a Client.
type Config struct {
	// BaseURL is an appliance origin with no path. The client adds SpecBasePath.
	BaseURL string
	// Credentials are sent to operationId create initially and on refresh.
	Credentials Credentials
	// HTTPClient is used for every request. nil means http.DefaultClient.
	HTTPClient *http.Client
}

// ListOptions map to optional listTroubleshootingIncidents query parameters.
type ListOptions struct {
	// PageSize maps to size. Zero means omit the parameter.
	PageSize int
	// StartEntityID maps to start_entity_id. Empty means omit the parameter.
	StartEntityID string
}

// Incident is the TroubleshootingIncident response schema.
type Incident struct {
	EntityID      string `json:"entity_id"`
	StartEntityID string `json:"start_entity_id"`
	Name          string `json:"name"`
	Status        string `json:"status"`
}

// Result is one complete paginated collection.
type Result struct {
	Incidents  []Incident
	TotalCount int
	// Pages counts successful list responses. A 401 does not count as a page.
	Pages int
}

// Client talks to one VCF Operations for Networks appliance.
//
// The private fields are present so implementations can make token replacement
// and TokenCreates safe under the race detector.
type Client struct {
	cfg  Config
	http *http.Client

	mu      sync.Mutex
	token   string
	creates int
}

// New validates cfg and returns a client. It performs no network I/O.
func New(cfg Config) (*Client, error) {
	panic("TODO: implement New")
}

// ListAll obtains an access token and follows every response cursor. If an
// authenticated page returns 401, it refreshes and retries that same page.
func (c *Client) ListAll(ctx context.Context, opts ListOptions) (*Result, error) {
	panic("TODO: implement ListAll")
}

// TokenCreates returns the number of successful create responses accepted by
// this client. It is safe to call concurrently with ListAll.
func (c *Client) TokenCreates() int {
	panic("TODO: implement TokenCreates")
}
