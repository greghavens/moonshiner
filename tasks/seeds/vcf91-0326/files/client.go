// Package vcfapolicy upserts a policy into VMware Cloud Foundation Automation
// 9.1 in a way that is safe to deliver more than once.
//
// The wire contract this package is written against is docs/contract.json,
// which is transcribed from the VCF Automation xAPIs reference rather than from
// a published specification. Read it before changing anything here.
package vcfapolicy

import (
	"context"
	"errors"
	"net/http"
	"time"
)

// Config configures a Client.
type Config struct {
	// BaseURL is the VCF Automation appliance root, e.g.
	// https://automation.example.com. Required.
	BaseURL string
	// Token is the bearer token sent on every request. Required.
	Token string
	// HTTPClient is used for every request. Nil means a fresh http.Client.
	HTTPClient *http.Client
	// MaxAttempts bounds how many times the upsert is delivered. Zero or less
	// means 4.
	MaxAttempts int
	// RetryDelay returns how long to wait before the next attempt, given the
	// 1-based number of the attempt that just failed. Nil means an exponential
	// default. A delay of zero or less is not waited on.
	RetryDelay func(attempt int) time.Duration
	// NewPolicyID mints the policy id when the caller does not supply one. Nil
	// means a random version 4 UUID.
	NewPolicyID func() string
}

// PolicySpec is the policy the caller wants in place. Every field other than
// TypeID is optional, and an optional field left at its zero value is absent
// from the request body.
type PolicySpec struct {
	// ID is the policy id. When empty the client mints one, once, and reuses
	// it for every delivery of this upsert.
	ID              string
	TypeID          string
	Name            string
	Description     string
	EnforcementType string
	OrgID           string
	ProjectID       string
	OPARegoCriteria string
	Criteria        map[string]any
	ScopeCriteria   map[string]any
	Definition      map[string]any
}

// Outcome says how a policy came to be in the state EnsurePolicy returned.
type Outcome string

const (
	// OutcomeCreated means the upsert was answered with 201.
	OutcomeCreated Outcome = "created"
	// OutcomeUpdated means the upsert was answered with 200.
	OutcomeUpdated Outcome = "updated"
	// OutcomeRecovered means a delivery whose outcome was never observed had
	// in fact landed, and was found by reading the policy back instead of
	// being sent again.
	OutcomeRecovered Outcome = "recovered"
)

// Result reports what EnsurePolicy did. Once a policy id has been chosen,
// PolicyID and Attempts remain populated on failure so the upsert can be
// reconciled by id.
type Result struct {
	PolicyID string
	Outcome  Outcome
	// Attempts counts deliveries of the upsert. Reads do not count.
	Attempts int
	Policy   Policy
}

// Policy is a policy as VCF Automation reports it.
type Policy struct {
	ID              string         `json:"id"`
	Name            string         `json:"name"`
	Description     string         `json:"description"`
	TypeID          string         `json:"typeId"`
	OrgID           string         `json:"orgId"`
	ProjectID       string         `json:"projectId"`
	EnforcementType string         `json:"enforcementType"`
	OPARegoCriteria string         `json:"opaRegoCriteria"`
	Criteria        map[string]any `json:"criteria"`
	ScopeCriteria   map[string]any `json:"scopeCriteria"`
	Definition      map[string]any `json:"definition"`
	CreatedAt       string         `json:"createdAt"`
	CreatedBy       string         `json:"createdBy"`
	LastUpdatedAt   string         `json:"lastUpdatedAt"`
	LastUpdatedBy   string         `json:"lastUpdatedBy"`
}

// APIError is a response the API returned that the client will not act on.
type APIError struct {
	// Operation is the contract operation id, "upsertPolicy" or "getPolicy".
	Operation  string
	Method     string
	Path       string
	StatusCode int
	Body       string
}

func (e *APIError) Error() string {
	return "vcfapolicy: not implemented"
}

// Client talks to the VCF Automation policy endpoints.
type Client struct {
	baseURL     string
	token       string
	httpClient  *http.Client
	maxAttempts int
	retryDelay  func(attempt int) time.Duration
	newPolicyID func() string
}

var errNotImplemented = errors.New("vcfapolicy: not implemented")

// New validates cfg and returns a Client.
func New(cfg Config) (*Client, error) {
	return nil, errNotImplemented
}

// EnsurePolicy puts spec in place and reports how it got there.
func (c *Client) EnsurePolicy(ctx context.Context, spec PolicySpec) (Result, error) {
	return Result{}, errNotImplemented
}
