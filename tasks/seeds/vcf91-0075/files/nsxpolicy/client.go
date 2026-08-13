// Package nsxpolicy implements a focused client for the VCF 9.1 NSX Policy API.
package nsxpolicy

import (
	"context"
	"errors"
	"net/http"
	"net/url"
)

// Credentials are the HTTP Basic credentials used for an NSX request.
type Credentials struct {
	Username string
	Password string
}

// ListOptions maps to the optional query parameters of ListGroupForDomain.
// Pointers distinguish an omitted value from an explicit false, zero, or empty value.
type ListOptions struct {
	Cursor                      *string
	IncludeMarkForDeleteObjects *bool
	IncludedFields              *string
	MemberTypes                 *string
	PageSize                    *int
	SortAscending               *bool
	SortBy                      *string
}

// Expression contains common fields used by the concrete NSX Group expression
// variants. ResourceType selects the concrete schema on the wire.
type Expression struct {
	ResourceType string   `json:"resource_type,omitempty"`
	MemberType   string   `json:"member_type,omitempty"`
	Key          string   `json:"key,omitempty"`
	Operator     string   `json:"operator,omitempty"`
	Value        string   `json:"value,omitempty"`
	Paths        []string `json:"paths,omitempty"`
	IPAddresses  []string `json:"ip_addresses,omitempty"`
}

// Tag is an opaque identifier attached to an NSX resource.
type Tag struct {
	Scope string `json:"scope,omitempty"`
	Tag   string `json:"tag,omitempty"`
}

// Group is the writable subset of the NSX Policy Group schema plus the fields
// returned by the two operations in the focused contract.
type Group struct {
	Revision     *int         `json:"_revision,omitempty"`
	Description  *string      `json:"description,omitempty"`
	DisplayName  *string      `json:"display_name,omitempty"`
	Expression   []Expression `json:"expression,omitempty"`
	GroupType    []string     `json:"group_type,omitempty"`
	ID           string       `json:"id,omitempty"`
	ResourceType string       `json:"resource_type,omitempty"`
	Tags         []Tag        `json:"tags,omitempty"`
}

// GroupListResult is returned by ListGroupForDomain.
type GroupListResult struct {
	Cursor        string  `json:"cursor,omitempty"`
	ResultCount   int     `json:"result_count,omitempty"`
	Results       []Group `json:"results"`
	SortAscending bool    `json:"sort_ascending,omitempty"`
	SortBy        string  `json:"sort_by,omitempty"`
}

// HTTPError reports a non-2xx NSX response.
type HTTPError struct {
	StatusCode int
	Body       string
}

func (e *HTTPError) Error() string { return http.StatusText(e.StatusCode) }

// Client is safe for concurrent use after construction.
type Client struct {
	baseURL     *url.URL
	httpClient  *http.Client
	credentials Credentials
}

// NewClient constructs a client using initial credentials.
func NewClient(baseURL string, httpClient *http.Client, initial Credentials) (*Client, error) {
	if baseURL == "" {
		return nil, errors.New("base URL is required")
	}
	parsed, err := url.Parse(baseURL)
	if err != nil {
		return nil, err
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return nil, errors.New("base URL must use http or https")
	}
	if parsed.Host == "" || parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, errors.New("base URL must contain a host and no query or fragment")
	}
	if initial.Username == "" || initial.Password == "" {
		return nil, errors.New("username and password are required")
	}
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{baseURL: parsed, httpClient: httpClient, credentials: initial}, nil
}

// RotateCredentials replaces credentials used by future attempts.
func (c *Client) RotateCredentials(next Credentials) error {
	if next.Username == "" || next.Password == "" {
		return errors.New("username and password are required")
	}
	c.credentials = next
	return nil
}

// ListGroups invokes ListGroupForDomain.
func (c *Client) ListGroups(context.Context, string, ListOptions) (GroupListResult, error) {
	return GroupListResult{}, errors.New("ListGroups not implemented")
}

// UpdateGroup invokes UpdateGroupForDomain.
func (c *Client) UpdateGroup(context.Context, string, string, Group) (Group, error) {
	return Group{}, errors.New("UpdateGroup not implemented")
}
