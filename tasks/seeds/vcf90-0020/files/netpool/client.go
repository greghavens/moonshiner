// Package netpool is a dependency-free client for the VMware Cloud Foundation 9.0
// SDDC Manager network pool operations named in docs/contract.json.
//
// The contract covers exactly two operations, getNetworkPool and
// createNetworkPool. Nothing else may be put on the wire.
package netpool

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
)

// Operation identifiers from the 9.0.0.0 specification.
const (
	OpGetNetworkPool    = "getNetworkPool"
	OpCreateNetworkPool = "createNetworkPool"
)

// duplicateNameErrorCode is the minor error code SDDC Manager returns from
// createNetworkPool when the requested pool name is already in use.
const duplicateNameErrorCode = "NETWORK_POOL_NAME_DUPLICATE"

const networkPoolsPath = "/v1/network-pools"

// IPRange is the IpPool schema: an inclusive range of addresses.
type IPRange struct {
	Start string `json:"start"`
	End   string `json:"end"`
}

// Network is the Network schema as returned by the API. Several of its
// properties are readOnly at 9.0.0.0 and are only ever populated by the server.
type Network struct {
	ID      string    `json:"id"`
	Type    string    `json:"type"`
	VLANID  int32     `json:"vlanId"`
	MTU     int32     `json:"mtu"`
	Subnet  string    `json:"subnet"`
	Mask    string    `json:"mask"`
	Gateway string    `json:"gateway"`
	IPPools []IPRange `json:"ipPools"`
	FreeIPs []string  `json:"freeIps"`
	UsedIPs []string  `json:"usedIps"`
}

// NetworkPool is the NetworkPool schema as returned by the API.
type NetworkPool struct {
	ID         string    `json:"id"`
	Name       string    `json:"name"`
	Networks   []Network `json:"networks"`
	HostsCount int32     `json:"hostsCount"`
}

// pageOfNetworkPool is the PageOfNetworkPool schema.
type pageOfNetworkPool struct {
	Elements []NetworkPool `json:"elements"`
}

// NetworkSpec is what a caller asks for in one network of a pool. IPPools is the
// only optional member; the rest are required at 9.0.0.0.
type NetworkSpec struct {
	Type    string
	VLANID  int32
	MTU     int32
	Subnet  string
	Mask    string
	Gateway string
	IPPools []IPRange
}

// NetworkPoolSpec is what a caller asks for in a pool.
type NetworkPoolSpec struct {
	Name     string
	Networks []NetworkSpec
}

// EnsureResult reports the outcome of EnsureNetworkPool.
type EnsureResult struct {
	// Pool is the pool that exists once the call returns.
	Pool NetworkPool
	// Created reports whether this specific call received a 201 from
	// createNetworkPool. Adopting a pool that was already there reports false.
	Created bool
}

// APIError is a response the server answered with a status outside 2xx.
type APIError struct {
	Operation string
	Status    int
	ErrorCode string
	Message   string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("%s: status %d: %s: %s", e.Operation, e.Status, e.ErrorCode, e.Message)
}

// IsDuplicateName reports whether the server rejected a create because a pool of
// that name already exists.
func (e *APIError) IsDuplicateName() bool {
	return e.Status == http.StatusBadRequest && e.ErrorCode == duplicateNameErrorCode
}

// Client talks to one SDDC Manager appliance.
type Client struct {
	baseURL string
	token   string
	http    *http.Client
}

// New returns a client for the appliance at baseURL, authenticating with the
// given bearer access token. A nil httpClient means http.DefaultClient.
func New(baseURL, token string, httpClient *http.Client) *Client {
	if httpClient == nil {
		httpClient = http.DefaultClient
	}
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		token:   token,
		http:    httpClient,
	}
}

// ListNetworkPools performs getNetworkPool. The operation declares no parameters
// at 9.0.0.0, so the whole collection comes back and filtering is done here.
func (c *Client) ListNetworkPools(ctx context.Context) ([]NetworkPool, error) {
	req, err := c.newRequest(ctx, http.MethodGet, networkPoolsPath, nil)
	if err != nil {
		return nil, err
	}
	var page pageOfNetworkPool
	if err := c.do(req, OpGetNetworkPool, http.StatusOK, &page); err != nil {
		return nil, err
	}
	return page.Elements, nil
}

// CreateNetworkPool performs createNetworkPool. It is the only mutating call in
// this package.
func (c *Client) CreateNetworkPool(ctx context.Context, spec NetworkPoolSpec) (NetworkPool, error) {
	encoded, err := json.Marshal(createBody(spec))
	if err != nil {
		return NetworkPool{}, fmt.Errorf("%s: encode body: %w", OpCreateNetworkPool, err)
	}
	req, err := c.newRequest(ctx, http.MethodPost, networkPoolsPath, encoded)
	if err != nil {
		return NetworkPool{}, err
	}
	var created NetworkPool
	if err := c.do(req, OpCreateNetworkPool, http.StatusCreated, &created); err != nil {
		return NetworkPool{}, err
	}
	return created, nil
}

// createBody builds the createNetworkPool request body.
//
// The read model already mirrors the NetworkPool schema, so create reuses it and
// keeps a single set of JSON tags for both directions.
func createBody(spec NetworkPoolSpec) NetworkPool {
	body := NetworkPool{Name: spec.Name}
	for _, n := range spec.Networks {
		body.Networks = append(body.Networks, Network{
			Type:    n.Type,
			VLANID:  n.VLANID,
			MTU:     n.MTU,
			Subnet:  n.Subnet,
			Mask:    n.Mask,
			Gateway: n.Gateway,
			IPPools: n.IPPools,
		})
	}
	return body
}

// EnsureNetworkPool brings the appliance to a state where a pool named
// spec.Name exists.
//
// Callers drive this from provisioning runs that are re-executed after partial
// failures, so the call is expected to be safe to repeat and safe to retry.
func (c *Client) EnsureNetworkPool(ctx context.Context, spec NetworkPoolSpec) (EnsureResult, error) {
	pool, err := c.CreateNetworkPool(ctx, spec)
	if err != nil {
		return EnsureResult{}, err
	}
	return EnsureResult{Pool: pool, Created: true}, nil
}

// findByName returns the pool carrying the given name, if the slice holds one.
func findByName(pools []NetworkPool, name string) (NetworkPool, bool) {
	for _, p := range pools {
		if p.Name == name {
			return p, true
		}
	}
	return NetworkPool{}, false
}

func (c *Client) newRequest(ctx context.Context, method, path string, body []byte) (*http.Request, error) {
	var reader io.Reader
	if body != nil {
		reader = bytes.NewReader(body)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, reader)
	if err != nil {
		return nil, fmt.Errorf("build %s %s: %w", method, path, err)
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.token)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	return req, nil
}

// do sends req and decodes a successful response into out. A status other than
// wantStatus becomes an *APIError.
func (c *Client) do(req *http.Request, operation string, wantStatus int, out any) error {
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("%s: %w", operation, err)
	}
	defer func() {
		_, _ = io.Copy(io.Discard, resp.Body)
		_ = resp.Body.Close()
	}()

	if resp.StatusCode != wantStatus {
		return decodeAPIError(operation, resp)
	}
	if out == nil {
		return nil
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return fmt.Errorf("%s: decode response: %w", operation, err)
	}
	return nil
}

func decodeAPIError(operation string, resp *http.Response) error {
	apiErr := &APIError{Operation: operation, Status: resp.StatusCode}
	var payload struct {
		ErrorCode string `json:"errorCode"`
		Message   string `json:"message"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&payload); err == nil {
		apiErr.ErrorCode = payload.ErrorCode
		apiErr.Message = payload.Message
	}
	return apiErr
}
