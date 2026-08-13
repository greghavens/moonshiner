// Package sddclcm is a dependency-free client for the VCF 9.1 SDDC LCM
// service. The wire contract it implements is recorded in docs/contract.json,
// whose provenance is recorded in docs/official_sources.json.
package sddclcm

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// DefaultPageSize is the largest page the service accepts for getTasks.
const DefaultPageSize = 50

// MaxPageSize mirrors the getTasks pageSize ceiling from the specification.
const MaxPageSize = 50

// DefaultConcurrency is the number of follower pages fetched at once.
const DefaultConcurrency = 4

// MinConcurrency is the smallest number of follower pages that may be in
// flight at once.
const MinConcurrency = 2

// LocalizableMessage mirrors the specification schema of the same name.
type LocalizableMessage struct {
	ID               string            `json:"id,omitempty"`
	DefaultMessage   string            `json:"defaultMessage,omitempty"`
	LocalizedMessage string            `json:"localizedMessage,omitempty"`
	Args             map[string]string `json:"args,omitempty"`
}

// TaskSummary mirrors the specification schema of the same name.
type TaskSummary struct {
	ID            string             `json:"id,omitempty"`
	Name          string             `json:"name,omitempty"`
	Description   LocalizableMessage `json:"description,omitempty"`
	Status        string             `json:"status,omitempty"`
	Type          string             `json:"type,omitempty"`
	CreatedBy     string             `json:"createdBy,omitempty"`
	UpdatedBy     string             `json:"updatedBy,omitempty"`
	ResourceID    string             `json:"resourceId,omitempty"`
	ResourceType  string             `json:"resourceType,omitempty"`
	CreateTime    string             `json:"createTime,omitempty"`
	StartTime     string             `json:"startTime,omitempty"`
	UpdateTime    string             `json:"updateTime,omitempty"`
	EndTime       string             `json:"endTime,omitempty"`
	CorrelationID string             `json:"correlationId,omitempty"`
	ParentTaskID  string             `json:"parentTaskId,omitempty"`
	Cancellable   bool               `json:"cancellable,omitempty"`
	Retriable     bool               `json:"retriable,omitempty"`
}

// PageMetadata mirrors the specification schema of the same name.
type PageMetadata struct {
	PageNumber    int `json:"pageNumber"`
	PageSize      int `json:"pageSize"`
	TotalElements int `json:"totalElements"`
	TotalPages    int `json:"totalPages"`
}

// PageOfTaskSummary mirrors the specification schema of the same name.
type PageOfTaskSummary struct {
	Elements     []TaskSummary `json:"elements"`
	PageMetadata PageMetadata  `json:"pageMetadata"`
}

// Component mirrors the specification schema of the same name.
type Component struct {
	ID             string `json:"id,omitempty"`
	ComponentType  string `json:"componentType,omitempty"`
	DeploymentType string `json:"deploymentType,omitempty"`
	Version        string `json:"version,omitempty"`
	Size           string `json:"size,omitempty"`
	FQDN           string `json:"fqdn,omitempty"`
	Scope          string `json:"scope,omitempty"`
}

// Components mirrors the specification schema of the same name.
type Components struct {
	Components []Component `json:"components"`
}

// Component scopes accepted by getComponents.
const (
	ScopeFleet    = "FLEET"
	ScopeInstance = "INSTANCE"
)

// TaskFilter carries the optional getTasks query filters. Every field is
// optional: a zero string or a nil pointer means "the caller did not set this
// filter", and the parameter must then be left off the request entirely.
type TaskFilter struct {
	Status             string
	Type               string
	CreatedBy          string
	Name               string
	Description        string
	ResourceID         string
	ResourceType       string
	StartTimeGt        *time.Time
	StartTimeLt        *time.Time
	UpdateTimeGt       *time.Time
	UpdateTimeLt       *time.Time
	EndTimeGt          *time.Time
	EndTimeLt          *time.Time
	IncludeSystemTasks *bool
}

// ListTasksOptions configures a full traversal of the getTasks collection.
type ListTasksOptions struct {
	Filter TaskFilter
	// PageSize is the requested page size. Zero selects DefaultPageSize and
	// any value is clamped to [1, MaxPageSize].
	PageSize int
	// Concurrency bounds how many follower pages are fetched at once. Zero
	// selects DefaultConcurrency and any value is raised to MinConcurrency.
	Concurrency int
}

// APIError reports a non-2xx response from the service.
type APIError struct {
	OperationID string
	Status      int
	Body        string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("sddclcm: %s returned HTTP %d: %s", e.OperationID, e.Status, e.Body)
}

// Client talks to one SDDC LCM service endpoint.
type Client struct {
	baseURL string
	token   string
	http    *http.Client
}

// NewClient builds a client. baseURL must already include the service base
// path recorded as server_base_path in the contract.
func NewClient(baseURL, bearerToken string, httpClient *http.Client) (*Client, error) {
	if strings.TrimSpace(baseURL) == "" {
		return nil, errors.New("sddclcm: baseURL is required")
	}
	if strings.TrimSpace(bearerToken) == "" {
		return nil, errors.New("sddclcm: bearer token is required")
	}
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 30 * time.Second}
	}
	return &Client{
		baseURL: strings.TrimSuffix(baseURL, "/"),
		token:   bearerToken,
		http:    httpClient,
	}, nil
}

// ListAllTasks retrieves the whole getTasks collection and returns it in the
// package's stable order.
//
// TODO(lcm): this walks only the first page, returns the elements in whatever
// order the service happened to emit them, and sends every filter parameter
// whether or not the caller set it.
func (c *Client) ListAllTasks(ctx context.Context, opts ListTasksOptions) ([]TaskSummary, error) {
	pageSize := opts.PageSize
	if pageSize == 0 {
		pageSize = DefaultPageSize
	}

	page, err := c.getTasksPage(ctx, opts.Filter, 0, pageSize)
	if err != nil {
		return nil, err
	}

	out := make([]TaskSummary, 0, len(page.Elements))
	out = append(out, page.Elements...)
	return out, nil
}

// getTasksPage performs one getTasks request.
func (c *Client) getTasksPage(ctx context.Context, filter TaskFilter, pageNumber, pageSize int) (*PageOfTaskSummary, error) {
	query := url.Values{}
	query.Set("pageNumber", strconv.Itoa(pageNumber))
	query.Set("pageSize", strconv.Itoa(pageSize))
	query.Set("status", filter.Status)
	query.Set("type", filter.Type)
	query.Set("createdBy", filter.CreatedBy)
	query.Set("name", filter.Name)
	query.Set("description", filter.Description)
	query.Set("resourceId", filter.ResourceID)
	query.Set("resourceType", filter.ResourceType)
	query.Set("startTimeGt", formatTime(filter.StartTimeGt))
	query.Set("startTimeLt", formatTime(filter.StartTimeLt))
	query.Set("updateTimeGt", formatTime(filter.UpdateTimeGt))
	query.Set("updateTimeLt", formatTime(filter.UpdateTimeLt))
	query.Set("endTimeGt", formatTime(filter.EndTimeGt))
	query.Set("endTimeLt", formatTime(filter.EndTimeLt))
	query.Set("includeSystemTasks", formatBool(filter.IncludeSystemTasks))

	var page PageOfTaskSummary
	if err := c.do(ctx, "getTasks", "/v1/tasks", query, &page); err != nil {
		return nil, err
	}
	return &page, nil
}

// ListComponents retrieves the SDDC and Fleet components. An empty scope means
// the caller did not filter by scope.
//
// TODO(lcm): scope is sent even when the caller left it unset.
func (c *Client) ListComponents(ctx context.Context, scope string) ([]Component, error) {
	query := url.Values{}
	query.Set("scope", scope)

	var body Components
	if err := c.do(ctx, "getComponents", "/v1/components", query, &body); err != nil {
		return nil, err
	}
	if body.Components == nil {
		return []Component{}, nil
	}
	return body.Components, nil
}

func (c *Client) do(ctx context.Context, operationID, path string, query url.Values, out any) error {
	target := c.baseURL + path
	if encoded := query.Encode(); encoded != "" {
		target += "?" + encoded
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return fmt.Errorf("sddclcm: %s: %w", operationID, err)
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	req.Header.Set("Accept", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("sddclcm: %s: %w", operationID, err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return fmt.Errorf("sddclcm: %s: reading response: %w", operationID, err)
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return &APIError{OperationID: operationID, Status: resp.StatusCode, Body: strings.TrimSpace(string(body))}
	}
	if err := json.Unmarshal(body, out); err != nil {
		return fmt.Errorf("sddclcm: %s: decoding response: %w", operationID, err)
	}
	return nil
}

func formatTime(t *time.Time) string {
	if t == nil {
		return ""
	}
	return t.UTC().Format(time.RFC3339)
}

func formatBool(b *bool) string {
	if b == nil {
		return ""
	}
	return strconv.FormatBool(*b)
}
