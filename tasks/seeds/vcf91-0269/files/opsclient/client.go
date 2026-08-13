// Package opsclient is a dependency-free client for the VMware Cloud
// Foundation Operations 9.1 API.
//
// It uses two operations from docs/contract.json:
//
//	acquireToken           POST /api/auth/token/acquire
//	getSymptomDefinitions  GET  /api/symptomdefinitions
//
// The contract is derived from the VCF Operations OpenAPI specification; its
// provenance is recorded in docs/official_sources.json.
package opsclient

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
)

// defaultPageSize is the pageSize default declared by getSymptomDefinitions.
const defaultPageSize = 1000

// tokenPrefix is the credential scheme for the Authorization header.
const tokenPrefix = "vRealizeOpsToken "

// maxResponseBytes caps how much of a response body is read.
const maxResponseBytes = 8 << 20

// Credentials is the username-password request body of acquireToken.
// AuthSource is optional.
type Credentials struct {
	Username   string
	Password   string
	AuthSource string
}

// Filter holds the optional search parameters of getSymptomDefinitions.
// A zero-valued field means the caller did not ask to filter on it.
// PageSize of zero or less means the operation's declared default.
type Filter struct {
	AdapterKind  string
	ResourceKind string
	Name         string
	IDs          []string
	PageSize     int
}

// SymptomDefinition is the subset of the symptom-definition schema this client
// surfaces.
type SymptomDefinition struct {
	ID              string `json:"id"`
	Name            string `json:"name"`
	AdapterKindKey  string `json:"adapterKindKey"`
	ResourceKindKey string `json:"resourceKindKey"`
}

// pageInfo is the page-info schema.
type pageInfo struct {
	Page       int `json:"page"`
	PageSize   int `json:"pageSize"`
	TotalCount int `json:"totalCount"`
}

// symptomDefinitionsPage is the symptom-definitions response schema.
type symptomDefinitionsPage struct {
	PageInfo           pageInfo            `json:"pageInfo"`
	SymptomDefinitions []SymptomDefinition `json:"symptomDefinitions"`
}

// authToken is the auth-token response schema.
type authToken struct {
	Token    string `json:"token"`
	Validity int64  `json:"validity"`
}

// credentialsPayload is the JSON encoding of username-password.
type credentialsPayload struct {
	Username   string `json:"username"`
	Password   string `json:"password"`
	AuthSource string `json:"authSource"`
}

// APIError is a non-2xx response from the appliance.
type APIError struct {
	OperationID string
	Status      int
	Body        string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("vcf operations: %s returned HTTP %d: %s", e.OperationID, e.Status, e.Body)
}

// Client talks to one VCF Operations appliance. It is safe for concurrent use
// once a token has been acquired.
type Client struct {
	baseURL string
	httpc   *http.Client

	mu    sync.RWMutex
	token string

	// collected accumulates the entries of the collection being retrieved.
	collected []SymptomDefinition
}

// New returns a client for the appliance rooted at baseURL, which must include
// the API base path (for example https://ops.example.com/suite-api). If httpc
// is nil, http.DefaultClient is used.
func New(baseURL string, httpc *http.Client) *Client {
	if httpc == nil {
		httpc = http.DefaultClient
	}
	return &Client{baseURL: strings.TrimRight(baseURL, "/"), httpc: httpc}
}

// Token returns the token most recently acquired, or "" if none.
func (c *Client) Token() string {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.token
}

// AcquireToken performs operationId acquireToken and stores the token for
// subsequent calls. The operation declares security: [], so the request is
// sent without an Authorization header.
func (c *Client) AcquireToken(ctx context.Context, creds Credentials) error {
	payload := credentialsPayload{
		Username:   creds.Username,
		Password:   creds.Password,
		AuthSource: creds.AuthSource,
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("vcf operations: encode acquireToken body: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		c.baseURL+"/api/auth/token/acquire", bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("vcf operations: build acquireToken request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")

	var out authToken
	if err := c.do(req, "acquireToken", &out); err != nil {
		return err
	}
	if out.Token == "" {
		return fmt.Errorf("vcf operations: acquireToken returned an empty token")
	}

	c.mu.Lock()
	c.token = out.Token
	c.mu.Unlock()
	return nil
}

// ListSymptomDefinitions performs operationId getSymptomDefinitions and returns
// the collection the filter selects.
func (c *Client) ListSymptomDefinitions(ctx context.Context, f Filter) ([]SymptomDefinition, error) {
	if c.Token() == "" {
		return nil, fmt.Errorf("vcf operations: no token; call AcquireToken first")
	}
	pageSize := f.PageSize
	if pageSize <= 0 {
		pageSize = defaultPageSize
	}

	c.collected = c.collected[:0]

	got, err := c.getSymptomDefinitionsPage(ctx, f, 0, pageSize)
	if err != nil {
		return nil, err
	}
	c.collected = append(c.collected, got.SymptomDefinitions...)

	return c.collected, nil
}

// getSymptomDefinitionsPage retrieves a single page of getSymptomDefinitions.
func (c *Client) getSymptomDefinitionsPage(ctx context.Context, f Filter, page, pageSize int) (*symptomDefinitionsPage, error) {
	q := url.Values{}
	q.Set("adapterKind", f.AdapterKind)
	q.Set("resourceKind", f.ResourceKind)
	q.Set("name", f.Name)
	for _, id := range f.IDs {
		q.Add("id", id)
	}
	q.Set("page", strconv.Itoa(page))
	q.Set("pageSize", strconv.Itoa(pageSize))

	req, err := http.NewRequestWithContext(ctx, http.MethodGet,
		c.baseURL+"/api/symptomdefinitions?"+q.Encode(), nil)
	if err != nil {
		return nil, fmt.Errorf("vcf operations: build getSymptomDefinitions request: %w", err)
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Authorization", tokenPrefix+c.Token())

	var out symptomDefinitionsPage
	if err := c.do(req, "getSymptomDefinitions", &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// do sends req and decodes a JSON response into out.
func (c *Client) do(req *http.Request, operationID string, out any) error {
	resp, err := c.httpc.Do(req)
	if err != nil {
		return fmt.Errorf("vcf operations: %s: %w", operationID, err)
	}
	defer func() {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, maxResponseBytes))
		_ = resp.Body.Close()
	}()

	body, err := io.ReadAll(io.LimitReader(resp.Body, maxResponseBytes))
	if err != nil {
		return fmt.Errorf("vcf operations: %s: read response: %w", operationID, err)
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return &APIError{OperationID: operationID, Status: resp.StatusCode, Body: strings.TrimSpace(string(body))}
	}
	if err := json.Unmarshal(body, out); err != nil {
		return fmt.Errorf("vcf operations: %s: decode response: %w", operationID, err)
	}
	return nil
}
