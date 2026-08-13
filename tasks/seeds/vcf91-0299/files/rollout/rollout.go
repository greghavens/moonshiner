// Package rollout applies a multi-tier application definition to VCF
// Operations for Networks 9.1 as an ordered sequence of REST calls.
//
// The wire contract is docs/contract.json, derived from
// specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml in
// vmware/vcf-api-specs. Only the operations that contract names may be called.
package rollout

import (
	"context"
	"net/http"
)

// Membership criteria discriminator values, per the spec's
// GroupMembershipCriteria.membership_type enum.
const (
	SearchMembership    = "SearchMembershipCriteria"
	IPAddressMembership = "IPAddressMembershipCriteria"
)

// Criterion is one membership rule for a tier.
//
// Type selects which of the two criteria bodies the rule carries: a
// SearchMembership criterion carries SearchEntityType/SearchFilter, and an
// IPAddressMembership criterion carries IPAddresses. The unused one is not
// part of the request.
type Criterion struct {
	Type             string
	SearchEntityType string
	SearchFilter     string
	IPAddresses      []string
}

// TierPlan is one tier to create under the application.
type TierPlan struct {
	Name     string
	Criteria []Criterion
}

// Plan is the desired application definition.
type Plan struct {
	ApplicationName string
	Tiers           []TierPlan
}

// StepStatus is the outcome of a single step of a rollout.
type StepStatus string

const (
	// StatusApplied means the call was made and the server accepted it.
	StatusApplied StepStatus = "applied"
	// StatusFailed means the call was made and the server rejected it, or the
	// call could not be completed.
	StatusFailed StepStatus = "failed"
	// StatusNotAttempted means no call was made for this step.
	StatusNotAttempted StepStatus = "not_attempted"
)

// Step records what happened for one step of a rollout.
type Step struct {
	// OperationID is the contract operationId this step invokes.
	OperationID string
	// Target is the application name for the addApplication step, and the tier
	// name for each addTier step.
	Target string
	// Status is the step outcome.
	Status StepStatus
	// EntityID is the entity_id the server assigned. Set only when applied.
	EntityID string
	// StatusCode is the HTTP status the server returned. Set only when the
	// call actually got a response.
	StatusCode int
	// Message is the server's ApiError.message. Set only when failed with a
	// response body carrying one.
	Message string
}

// Report is the outcome of a whole rollout.
type Report struct {
	// ApplicationID is the entity_id of the created application, empty if the
	// application was never created.
	ApplicationID string
	// Steps has one entry per planned step, in execution order: the
	// addApplication step first, then one addTier step per Plan.Tiers entry,
	// in Plan order. Every planned step is present regardless of outcome.
	Steps []Step
	// Failed reports whether any step failed.
	Failed bool
}

// Client calls one VCF Operations for Networks instance.
type Client struct {
	baseURL string
	token   string
	httpc   *http.Client
}

// NewClient returns a Client for the instance at baseURL, e.g.
// "https://vcfops-networks.example.com". The API base path is appended by the
// client; baseURL must not include it. If httpc is nil, http.DefaultClient is
// used. token is the bare API token.
func NewClient(baseURL, token string, httpc *http.Client) *Client {
	if httpc == nil {
		httpc = http.DefaultClient
	}
	return &Client{baseURL: baseURL, token: token, httpc: httpc}
}

// Apply creates the application and then its tiers, in Plan order, stopping at
// the first step that fails.
//
// Apply always returns a non-nil Report describing every planned step,
// including when it returns a non-nil error. The error is non-nil exactly when
// the rollout did not fully apply, and it describes the step that failed.
// Callers rely on the Report to learn which earlier steps were already applied
// and are therefore left behind on the server.
//
// Because a partly-applied rollout is the case where accuracy matters most,
// Apply does not simply trust its own bookkeeping: when a step fails after the
// application was created, it calls listApplicationTiers once for that
// application and keeps a tier marked applied only if the server lists it. A
// rollout in which every step succeeds makes no such confirmation call.
func (c *Client) Apply(ctx context.Context, p Plan) (*Report, error) {
	panic("rollout: Apply is not implemented")
}
