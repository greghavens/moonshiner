// Package opsrollout applies a multi-step configuration change to a VMware
// Cloud Foundation Operations 9.1 appliance and reports what the appliance
// actually accepted.
//
// The change is the ordered sequence in docs/contract.json: acquire a token,
// create a custom group, assign a policy to that group, then create a
// notification rule for it. The appliance has no transaction spanning the four
// operations, so a step that fails leaves the earlier steps applied. The report
// is the only record of what happened.
//
// The package uses net/http from the standard library and nothing else.
package opsrollout

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
)

// Step names, in the order the change applies them.
const (
	StepAcquireToken = "acquire-token"
	StepCreateGroup  = "create-custom-group"
	StepAssignPolicy = "assign-policy"
	StepCreateRule   = "create-notification-rule"
)

// Operation ids from docs/contract.json, one per step.
const (
	OpAcquireToken = "acquireToken"
	OpCreateGroup  = "createCustomGroup"
	OpAssignPolicy = "assignPolicy"
	OpCreateRule   = "createNotificationPluginRule"
)

// tokenPrefix is contract.clientRules.transport.authorizationHeader.valuePrefix.
const tokenPrefix = "vRealizeOpsToken "

// ruleTypeAlert is the notification-rule.ruleType this change creates.
const ruleTypeAlert = "ALERT"

// StepStatus is the outcome of one step of the change.
type StepStatus string

const (
	// StatusSucceeded means the appliance answered with the operation's
	// declared success status.
	StatusSucceeded StepStatus = "SUCCEEDED"
	// StatusFailed means the step was attempted and did not succeed.
	StatusFailed StepStatus = "FAILED"
	// StatusSkipped means the step was never attempted because an earlier step
	// failed.
	StatusSkipped StepStatus = "SKIPPED"
)

// Credentials authenticate against the appliance. AuthSource is optional and
// selects a non-local authentication source.
type Credentials struct {
	Username   string
	Password   string
	AuthSource string
}

// GroupSpec describes the custom group to create. IncludedResourceIDs and
// ExcludedResourceIDs are optional.
type GroupSpec struct {
	Name                  string
	AdapterKindKey        string
	ResourceKindKey       string
	AutoResolveMembership bool
	IncludedResourceIDs   []string
	ExcludedResourceIDs   []string
}

// ResourceAssignment assigns a policy to a resource and the given depth of its
// hierarchy. Both fields are required by the appliance; a depth of 0 means the
// resource itself.
type ResourceAssignment struct {
	ResourceID string
	Depth      int
}

// RuleSpec describes the notification rule to create. TemplateID,
// AlertDefinitionIDs and Criticalities are optional.
type RuleSpec struct {
	Name               string
	PluginID           string
	Enabled            bool
	TemplateID         string
	AlertDefinitionIDs []string
	Criticalities      []string
}

// Change is one multi-step configuration change.
type Change struct {
	Credentials         Credentials
	Group               GroupSpec
	PolicyID            string
	ResourceAssignments []ResourceAssignment
	Rule                RuleSpec
}

// StepReport is the outcome of one step.
type StepReport struct {
	// Name is the step name, one of the Step* constants.
	Name string
	// OperationID is the contract operation the step calls.
	OperationID string
	// Status is the outcome.
	Status StepStatus
	// HTTPStatus is the status the appliance answered with, or 0 when the step
	// was not attempted.
	HTTPStatus int
	// Detail carries what the appliance returned for a succeeded step: the
	// identifier it assigned or the value it echoed back. It is empty for a
	// step that failed or was skipped.
	Detail string
	// Err is the failure message for a failed step, empty otherwise.
	Err string
}

// Report is the outcome of a whole change.
type Report struct {
	// Steps holds one entry per step of the change, in order.
	Steps []StepReport
	// Failed is true when any step failed.
	Failed bool
}

// APIError is a response from the appliance that is not the operation's
// declared success status.
type APIError struct {
	OperationID string
	Status      int
	Message     string
}

func (e *APIError) Error() string {
	if e.Message == "" {
		return fmt.Sprintf("%s: appliance answered %d", e.OperationID, e.Status)
	}
	return fmt.Sprintf("%s: appliance answered %d: %s", e.OperationID, e.Status, e.Message)
}

// Client applies changes to one appliance.
type Client struct {
	baseURL string
	hc      *http.Client

	mu    sync.RWMutex
	token string

	// steps accumulates the report of the change being applied.
	steps []StepReport
}

// New returns a client for the appliance rooted at baseURL, which must include
// the /suite-api base path. hc may be nil, in which case http.DefaultClient is
// used.
func New(baseURL string, hc *http.Client) *Client {
	if hc == nil {
		hc = http.DefaultClient
	}
	return &Client{baseURL: strings.TrimSuffix(baseURL, "/"), hc: hc}
}

// ---------------------------------------------------------------------------
// Wire payloads. Field names and required-ness come from docs/contract.json.
// ---------------------------------------------------------------------------

type credentialsPayload struct {
	Username   string `json:"username"`
	Password   string `json:"password"`
	AuthSource string `json:"authSource"`
}

type authToken struct {
	Token     string `json:"token"`
	Validity  int64  `json:"validity"`
	ExpiresAt string `json:"expiresAt"`
}

type resourceKeyPayload struct {
	Name            string `json:"name"`
	AdapterKindKey  string `json:"adapterKindKey"`
	ResourceKindKey string `json:"resourceKindKey"`
}

type membershipPayload struct {
	IncludedResources []string `json:"includedResources"`
	ExcludedResources []string `json:"excludedResources"`
}

type customGroupPayload struct {
	ResourceKey           resourceKeyPayload `json:"resourceKey"`
	MembershipDefinition  membershipPayload  `json:"membershipDefinition"`
	AutoResolveMembership bool               `json:"autoResolveMembership"`
}

type customGroup struct {
	ID string `json:"id"`
}

type resourceAssignmentPayload struct {
	ResourceID string `json:"resourceId"`
	Depth      int    `json:"depth,omitempty"`
}

type policyAssignmentPayload struct {
	GroupIDs            []string                    `json:"groupIds"`
	ResourceAssignments []resourceAssignmentPayload `json:"resourceAssignments"`
}

type policyAssociations struct {
	AssignedGroupIDs    []string `json:"assignedGroupIds"`
	AssignedResourceIDs []string `json:"assignedResourceIds"`
	FailedGroupIDs      []string `json:"failedGroupIds"`
	FailedResourceIDs   []string `json:"failedResourceIds"`
}

type strValues struct {
	Values []string `json:"values"`
}

type notificationRulePayload struct {
	Name                     string     `json:"name"`
	PluginID                 string     `json:"pluginId"`
	Enabled                  bool       `json:"enabled"`
	RuleType                 string     `json:"ruleType"`
	TemplateID               string     `json:"templateId"`
	Criticalities            []string   `json:"criticalities"`
	AlertDefinitionIDFilters *strValues `json:"alertDefinitionIdFilters"`
}

type notificationRule struct {
	ID string `json:"id"`
}

// ---------------------------------------------------------------------------
// Applying a change
// ---------------------------------------------------------------------------

// Apply runs the change against the appliance and returns a report of it.
func (c *Client) Apply(ctx context.Context, ch Change) (*Report, error) {
	c.steps = c.steps[:0]

	expiresAt, err := c.acquireToken(ctx, ch.Credentials)
	if err != nil {
		return nil, err
	}
	c.succeeded(StepAcquireToken, OpAcquireToken, http.StatusOK, expiresAt)

	if _, err := c.createCustomGroup(ctx, ch.Group); err != nil {
		return nil, err
	}
	c.succeeded(StepCreateGroup, OpCreateGroup, http.StatusCreated, ch.Group.Name)

	assoc, err := c.assignPolicy(ctx, ch.PolicyID, ch.Group.Name, ch.ResourceAssignments)
	if err != nil {
		return nil, err
	}
	c.succeeded(StepAssignPolicy, OpAssignPolicy, http.StatusOK, strings.Join(assoc.AssignedGroupIDs, ","))

	rule, err := c.createNotificationRule(ctx, ch.Rule)
	if err != nil {
		return nil, err
	}
	c.succeeded(StepCreateRule, OpCreateRule, http.StatusCreated, rule.ID)

	return &Report{Steps: c.steps}, nil
}

// succeeded records a step that the appliance accepted.
func (c *Client) succeeded(name, operationID string, status int, detail string) {
	c.steps = append(c.steps, StepReport{
		Name:        name,
		OperationID: operationID,
		Status:      StatusSucceeded,
		HTTPStatus:  status,
		Detail:      detail,
	})
}

// acquireToken performs step 1 and caches the token for the later steps.
func (c *Client) acquireToken(ctx context.Context, creds Credentials) (string, error) {
	body := credentialsPayload{
		Username:   creds.Username,
		Password:   creds.Password,
		AuthSource: creds.AuthSource,
	}
	var out authToken
	// acquireToken declares security: [] and is sent unauthenticated.
	if err := c.do(ctx, http.MethodPost, "/api/auth/token/acquire", OpAcquireToken,
		http.StatusOK, false, body, &out); err != nil {
		return "", err
	}
	if out.Token == "" {
		return "", fmt.Errorf("%s: appliance returned an empty token", OpAcquireToken)
	}
	c.mu.Lock()
	c.token = out.Token
	c.mu.Unlock()
	return out.ExpiresAt, nil
}

// createCustomGroup performs step 2.
func (c *Client) createCustomGroup(ctx context.Context, g GroupSpec) (*customGroup, error) {
	body := customGroupPayload{
		ResourceKey: resourceKeyPayload{
			Name:            g.Name,
			AdapterKindKey:  g.AdapterKindKey,
			ResourceKindKey: g.ResourceKindKey,
		},
		MembershipDefinition: membershipPayload{
			IncludedResources: append([]string{}, g.IncludedResourceIDs...),
			ExcludedResources: append([]string{}, g.ExcludedResourceIDs...),
		},
		AutoResolveMembership: g.AutoResolveMembership,
	}
	var out customGroup
	if err := c.do(ctx, http.MethodPost, "/api/resources/groups", OpCreateGroup,
		http.StatusCreated, true, body, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// assignPolicy performs step 3.
func (c *Client) assignPolicy(ctx context.Context, policyID, groupID string, assignments []ResourceAssignment) (*policyAssociations, error) {
	payload := policyAssignmentPayload{
		GroupIDs:            []string{groupID},
		ResourceAssignments: make([]resourceAssignmentPayload, 0, len(assignments)),
	}
	for _, a := range assignments {
		payload.ResourceAssignments = append(payload.ResourceAssignments, resourceAssignmentPayload{
			ResourceID: a.ResourceID,
			Depth:      a.Depth,
		})
	}
	var out policyAssociations
	path := "/api/policies/" + policyID + "/assign"
	if err := c.do(ctx, http.MethodPut, path, OpAssignPolicy,
		http.StatusOK, true, payload, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// createNotificationRule performs step 4.
func (c *Client) createNotificationRule(ctx context.Context, r RuleSpec) (*notificationRule, error) {
	body := notificationRulePayload{
		Name:                     r.Name,
		PluginID:                 r.PluginID,
		Enabled:                  r.Enabled,
		RuleType:                 ruleTypeAlert,
		TemplateID:               r.TemplateID,
		Criticalities:            append([]string{}, r.Criticalities...),
		AlertDefinitionIDFilters: &strValues{Values: append([]string{}, r.AlertDefinitionIDs...)},
	}
	var out notificationRule
	if err := c.do(ctx, http.MethodPost, "/api/notifications/rules", OpCreateRule,
		http.StatusCreated, true, body, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

// do sends one JSON request and decodes a JSON response. It returns an
// *APIError when the appliance answers with anything but wantStatus.
func (c *Client) do(ctx context.Context, method, path, operationID string, wantStatus int, authenticated bool, body, out any) error {
	encoded, err := json.Marshal(body)
	if err != nil {
		return fmt.Errorf("%s: encode request: %w", operationID, err)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, bytes.NewReader(encoded))
	if err != nil {
		return fmt.Errorf("%s: build request: %w", operationID, err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	if authenticated {
		token := c.currentToken()
		if token == "" {
			return fmt.Errorf("%s: no token; acquireToken must succeed first", operationID)
		}
		req.Header.Set("Authorization", tokenPrefix+token)
	}

	resp, err := c.hc.Do(req)
	if err != nil {
		return fmt.Errorf("%s: %w", operationID, err)
	}
	defer resp.Body.Close()

	payload, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("%s: read response: %w", operationID, err)
	}
	if resp.StatusCode != wantStatus {
		return &APIError{
			OperationID: operationID,
			Status:      resp.StatusCode,
			Message:     applianceMessage(payload),
		}
	}
	if out == nil {
		return nil
	}
	if err := json.Unmarshal(payload, out); err != nil {
		return fmt.Errorf("%s: decode response: %w", operationID, err)
	}
	return nil
}

func (c *Client) currentToken() string {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.token
}

// applianceMessage extracts the message from an error body, falling back to the
// raw body when it is not the shape the appliance normally returns.
func applianceMessage(payload []byte) string {
	var body struct {
		Message string `json:"message"`
	}
	if err := json.Unmarshal(payload, &body); err == nil && body.Message != "" {
		return body.Message
	}
	return strings.TrimSpace(string(payload))
}

// statusOf reports the HTTP status an error carries, or 0 when it is not an
// appliance response.
func statusOf(err error) int {
	var apiErr *APIError
	if errors.As(err, &apiErr) {
		return apiErr.Status
	}
	return 0
}
