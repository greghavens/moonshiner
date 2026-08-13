// Package vcfops is a dependency-free client for the slice of the VMware Cloud
// Foundation Operations 9.1 API that manages an API session token: acquiring a
// token, making an authenticated call with it, and releasing it.
//
// The wire contract is pinned in docs/contract.json, derived from
// specifications/vcf-operations/vcf-operations-openapi.json in
// github.com/vmware/vcf-api-specs. Provenance is in docs/official_sources.json.
package vcfops

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

// Wire constants from docs/contract.json.
const (
	basePath = "/suite-api"

	acquireTokenPath = basePath + "/api/auth/token/acquire"
	currentUserPath  = basePath + "/api/auth/currentuser"
	releaseTokenPath = basePath + "/api/auth/token/release"

	authHeader  = "Authorization"
	tokenPrefix = "OpsToken "

	contentTypeJSON = "application/json"
)

var (
	// ErrNotAuthenticated is returned when a call needs a token and the client
	// has never successfully authenticated.
	ErrNotAuthenticated = errors.New("vcfops: client has no active token")

	// ErrClosed is returned once Close has been called.
	ErrClosed = errors.New("vcfops: client is closed")
)

// APIError reports a non-success HTTP status from VCF Operations.
type APIError struct {
	OperationID string
	StatusCode  int
	Body        string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("vcfops: %s: unexpected status %d: %s", e.OperationID, e.StatusCode, e.Body)
}

// Credentials identify a principal to acquireToken.
//
// AuthSource is optional. Per docs/contract.json the "authSource" property is
// omitted from the request body when the caller does not supply one.
type Credentials struct {
	Username   string
	Password   string
	AuthSource string
}

// User is the subset of the spec "user" schema this client reads.
type User struct {
	ID        string   `json:"id,omitempty"`
	Username  string   `json:"username"`
	FirstName string   `json:"firstName,omitempty"`
	LastName  string   `json:"lastName,omitempty"`
	Enabled   bool     `json:"enabled,omitempty"`
	RoleNames []string `json:"roleNames,omitempty"`
}

// usernamePassword encodes the spec "username-password" schema.
type usernamePassword struct {
	Username   string `json:"username"`
	Password   string `json:"password"`
	AuthSource string `json:"authSource"`
}

// authToken decodes the spec "auth-token" schema.
type authToken struct {
	Token     string   `json:"token"`
	Validity  int64    `json:"validity"`
	ExpiresAt string   `json:"expiresAt,omitempty"`
	Roles     []string `json:"roles,omitempty"`
}

// session is one credential generation: the token acquired for a given set of
// credentials, used by every request issued while it is the active generation.
type session struct {
	token string
}

// Client is safe for concurrent use.
type Client struct {
	baseURL string
	httpc   *http.Client

	mu     sync.Mutex
	active *session
	closed bool
}

// NewClient returns a client for the VCF Operations instance at baseURL, which
// must not include the /suite-api base path. A nil httpc uses http.DefaultClient.
func NewClient(baseURL string, httpc *http.Client) *Client {
	if httpc == nil {
		httpc = http.DefaultClient
	}
	return &Client{
		baseURL: strings.TrimSuffix(baseURL, "/"),
		httpc:   httpc,
	}
}

// Authenticate acquires the client's first token. It is an error to call it on
// a client that already has an active token; use Rotate for that.
func (c *Client) Authenticate(ctx context.Context, creds Credentials) error {
	c.mu.Lock()
	switch {
	case c.closed:
		c.mu.Unlock()
		return ErrClosed
	case c.active != nil:
		c.mu.Unlock()
		return errors.New("vcfops: client already has an active token; use Rotate")
	}
	c.mu.Unlock()

	tok, err := c.acquireToken(ctx, creds)
	if err != nil {
		return err
	}

	c.mu.Lock()
	var installErr error
	switch {
	case c.closed:
		installErr = ErrClosed
	case c.active != nil:
		// Another Authenticate or Rotate won the race while acquireToken was
		// running. Keep its generation and dispose of the unused token below.
		installErr = errors.New("vcfops: client already has an active token; use Rotate")
	default:
		c.active = &session{token: tok.Token}
	}
	c.mu.Unlock()

	if installErr != nil {
		_ = c.releaseToken(context.WithoutCancel(ctx), tok.Token)
	}
	return installErr
}

// CurrentUser calls getCurrentUser with whichever token is active when the call
// starts.
func (c *Client) CurrentUser(ctx context.Context) (*User, error) {
	s, err := c.currentSession()
	if err != nil {
		return nil, err
	}
	return c.getCurrentUser(ctx, s.token)
}

// Rotate moves the client onto creds: it acquires a token for the new
// credentials, makes that token the one new callers use, and releases the token
// it replaced.
func (c *Client) Rotate(ctx context.Context, creds Credentials) error {
	tok, err := c.acquireToken(ctx, creds)
	if err != nil {
		return err
	}

	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		_ = c.releaseToken(context.WithoutCancel(ctx), tok.Token)
		return ErrClosed
	}
	retired := c.active
	c.active = &session{token: tok.Token}
	c.mu.Unlock()

	if retired == nil {
		return nil
	}
	return c.releaseToken(ctx, retired.token)
}

// Close releases the active token, if any. Close is idempotent.
func (c *Client) Close(ctx context.Context) error {
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return nil
	}
	c.closed = true
	active := c.active
	c.active = nil
	c.mu.Unlock()

	if active == nil {
		return nil
	}
	return c.releaseToken(ctx, active.token)
}

func (c *Client) currentSession() (*session, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	switch {
	case c.closed:
		return nil, ErrClosed
	case c.active == nil:
		return nil, ErrNotAuthenticated
	}
	return c.active, nil
}

// acquireToken implements operationId acquireToken. It sends no Authorization
// header: the specification gives this operation an empty security list.
func (c *Client) acquireToken(ctx context.Context, creds Credentials) (*authToken, error) {
	body, err := json.Marshal(usernamePassword{
		Username:   creds.Username,
		Password:   creds.Password,
		AuthSource: creds.AuthSource,
	})
	if err != nil {
		return nil, fmt.Errorf("vcfops: acquireToken: encode request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+acquireTokenPath, bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("vcfops: acquireToken: %w", err)
	}
	req.Header.Set("Content-Type", contentTypeJSON)
	req.Header.Set("Accept", contentTypeJSON)

	var tok authToken
	if err := c.do(req, "acquireToken", &tok); err != nil {
		return nil, err
	}
	if tok.Token == "" {
		return nil, errors.New("vcfops: acquireToken: response carried no token")
	}
	return &tok, nil
}

// getCurrentUser implements operationId getCurrentUser.
func (c *Client) getCurrentUser(ctx context.Context, token string) (*User, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+currentUserPath, nil)
	if err != nil {
		return nil, fmt.Errorf("vcfops: getCurrentUser: %w", err)
	}
	req.Header.Set(authHeader, tokenPrefix+token)
	req.Header.Set("Accept", contentTypeJSON)

	var u User
	if err := c.do(req, "getCurrentUser", &u); err != nil {
		return nil, err
	}
	return &u, nil
}

// releaseToken implements operationId releaseToken. The operation takes no
// request body and returns no response body.
func (c *Client) releaseToken(ctx context.Context, token string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+releaseTokenPath, nil)
	if err != nil {
		return fmt.Errorf("vcfops: releaseToken: %w", err)
	}
	req.Header.Set(authHeader, tokenPrefix+token)

	return c.do(req, "releaseToken", nil)
}

// do sends req and, when out is non-nil, decodes a JSON success body into it.
func (c *Client) do(req *http.Request, operationID string, out any) error {
	resp, err := c.httpc.Do(req)
	if err != nil {
		return fmt.Errorf("vcfops: %s: %w", operationID, err)
	}
	defer func() {
		_, _ = io.Copy(io.Discard, resp.Body)
		_ = resp.Body.Close()
	}()

	if resp.StatusCode != http.StatusOK {
		snippet, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return &APIError{
			OperationID: operationID,
			StatusCode:  resp.StatusCode,
			Body:        strings.TrimSpace(string(snippet)),
		}
	}
	if out == nil {
		return nil
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return fmt.Errorf("vcfops: %s: decode response: %w", operationID, err)
	}
	return nil
}
