// Package onboarding registers a vCenter as a data source in VCF Operations
// for Networks (VCF 9.1).
//
// Status: first draft. This was written against a rendered API reference page
// rather than against the OpenAPI document, and it does not yet match
// docs/contract.json.
package onboarding

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
)

// ErrInvalidRequest is returned when a VCenterOnboardRequest cannot be turned
// into a legal request body. Errors of this kind are raised before any HTTP
// call is made.
var ErrInvalidRequest = errors.New("onboarding: invalid request")

// Credentials are the vCenter credentials Operations for Networks will use to
// poll the data source.
type Credentials struct {
	Username string
	Password string
}

// VCenterOnboardRequest describes the vCenter to onboard.
type VCenterOnboardRequest struct {
	// IP and FQDN are mutually exclusive: set exactly one.
	IP   string
	FQDN string

	// ProxyID is the collector VM that will own this data source. Required.
	ProxyID string

	// Nickname is the friendly name for the data source. Required.
	Nickname string

	// Notes is optional free text.
	Notes string

	// Enabled is a tri-state. nil means "say nothing and let the server apply
	// its own default".
	Enabled *bool

	// IPFIXEnabled asks the precheck to also verify IPFIX configuration. It is
	// a precheck-side concern only.
	IPFIXEnabled bool

	Credentials Credentials
}

// ValidationOutcome is the verdict the precheck returned.
type ValidationOutcome struct {
	Code    int
	Message string
}

// OnboardResult describes a data source that was created.
type OnboardResult struct {
	EntityID   string
	EntityType string
	Validation ValidationOutcome
}

// PrecheckError reports that the precheck refused the vCenter. When
// OnboardVCenter returns a *PrecheckError, no data source was created.
type PrecheckError struct {
	Code    int
	Message string
}

func (e *PrecheckError) Error() string {
	return fmt.Sprintf("onboarding: precheck failed: code %d: %s", e.Code, e.Message)
}

// Client talks to one Operations for Networks appliance.
type Client struct {
	baseURL string
	token   string
	http    *http.Client
}

// New builds a Client. baseURL is the appliance root, without the API base
// path, e.g. https://opsnet.example.com. If hc is nil, http.DefaultClient is
// used.
func New(baseURL, token string, hc *http.Client) (*Client, error) {
	if baseURL == "" {
		return nil, fmt.Errorf("%w: baseURL is empty", ErrInvalidRequest)
	}
	if hc == nil {
		hc = http.DefaultClient
	}
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		token:   token,
		http:    hc,
	}, nil
}

// dataSourceBody is the JSON body sent to the API.
//
// TODO: the two operations do not actually share a request schema. Sharing one
// struct is what makes this draft send nickname/notes/enabled to the precheck
// and drop ipfix_enabled entirely.
type dataSourceBody struct {
	IP          string          `json:"ip"`
	FQDN        string          `json:"fqdn"`
	ProxyID     string          `json:"proxy_id"`
	Nickname    string          `json:"nickname"`
	Notes       string          `json:"notes"`
	Enabled     bool            `json:"enabled"`
	Credentials credentialsBody `json:"credentials"`
}

type credentialsBody struct {
	Username string `json:"username"`
	Password string `json:"password"`
}

func (r VCenterOnboardRequest) body() dataSourceBody {
	b := dataSourceBody{
		IP:       r.IP,
		FQDN:     r.FQDN,
		ProxyID:  r.ProxyID,
		Nickname: r.Nickname,
		Notes:    r.Notes,
		Credentials: credentialsBody{
			Username: r.Credentials.Username,
			Password: r.Credentials.Password,
		},
	}
	if r.Enabled != nil {
		b.Enabled = *r.Enabled
	}
	return b
}

// OnboardVCenter registers the vCenter as a data source.
//
// TODO: the precheck result is fetched but not acted on, so a vCenter that
// fails validation is still created.
func (c *Client) OnboardVCenter(ctx context.Context, req VCenterOnboardRequest) (*OnboardResult, error) {
	body := req.body()

	status, raw, err := c.post(ctx, "/api/ni/data-sources/vcenters/validate", body)
	if err != nil {
		return nil, err
	}

	var outcome ValidationOutcome
	if status == http.StatusOK {
		if err := json.Unmarshal(raw, &outcome); err != nil {
			return nil, fmt.Errorf("onboarding: decode precheck response: %w", err)
		}
	}

	status, raw, err = c.post(ctx, "/api/ni/data-sources/vcenters", body)
	if err != nil {
		return nil, err
	}
	if status != http.StatusCreated {
		return nil, fmt.Errorf("onboarding: create data source: unexpected status %d: %s", status, raw)
	}

	var created struct {
		EntityID   string `json:"entity_id"`
		EntityType string `json:"entity_type"`
	}
	if err := json.Unmarshal(raw, &created); err != nil {
		return nil, fmt.Errorf("onboarding: decode create response: %w", err)
	}

	return &OnboardResult{
		EntityID:   created.EntityID,
		EntityType: created.EntityType,
		Validation: outcome,
	}, nil
}

func (c *Client) post(ctx context.Context, path string, body any) (int, []byte, error) {
	buf, err := json.Marshal(body)
	if err != nil {
		return 0, nil, fmt.Errorf("onboarding: encode request body: %w", err)
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(buf))
	if err != nil {
		return 0, nil, fmt.Errorf("onboarding: build request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Accept", "application/json")
	httpReq.Header.Set("Authorization", "Bearer "+c.token)

	resp, err := c.http.Do(httpReq)
	if err != nil {
		return 0, nil, fmt.Errorf("onboarding: POST %s: %w", path, err)
	}
	defer resp.Body.Close()

	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return 0, nil, fmt.Errorf("onboarding: read response body: %w", err)
	}
	return resp.StatusCode, raw, nil
}
