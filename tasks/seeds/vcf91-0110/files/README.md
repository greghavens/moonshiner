# Resumable vCenter inventory client

Implement package `example.com/vcfinventory/vcenter` using only the Go standard
library. The reduced VCF 9.1 wire contract in
[`docs/contract.json`](docs/contract.json) was projected directly from
`specifications/vsphere/openapi/automation/vcenter.yaml` in VMware's
Apache-2.0 `vmware/vcf-api-specs` repository. Its immutable source commit and
the exact operationIds are recorded in
[`docs/official_sources.json`](docs/official_sources.json). The YAML
specification, not a rendered documentation page, is authoritative.

The supplied `internal/mockvcenter` package starts an ephemeral IPv4 loopback
server, serves only the three operations named by the focused contract, and
offers a race-safe request log. Tests must not contact a live VMware endpoint.

## Required API

The protected `vcenter/types.go` declares all public data types. Implement:

```go
func NewClient(
    ctx context.Context,
    baseURL, username, password string,
    httpClient *http.Client,
) (*Client, error)

func (c *Client) ListDatacenters(
    ctx context.Context,
    filter DatacenterFilter,
) ([]DatacenterSummary, error)

func (c *Client) ListVMs(
    ctx context.Context,
    filter VMFilter,
) ([]VMSummary, error)

func (c *Client) CollectInventory(
    ctx context.Context,
) (InventorySnapshot, error)
```

`Client` must be safe for concurrent calls.

## Construction and session creation

Treat `baseURL` as an HTTP(S) origin, without `/api`. Reject an empty value,
embedded credentials, a query, a fragment, or a non-root path. Reject blank or
control-character-bearing credentials and a username containing `:` before
opening a connection. Do not mutate the caller's `http.Client`, and do not
follow redirects.

Construction creates the initial session with operation
`Cis.Session_create`:

- bodyless `POST /api/session`;
- Basic authentication over the UTF-8 bytes of `username:password`;
- `Accept: application/json`;
- no `vmware-api-session-id` and no `Content-Type`;
- exactly HTTP 201 with a nonblank JSON string token.

Do not retry session creation.

## Inventory list operations

`Vcenter.Datacenter_list` is `GET /api/vcenter/datacenter`.
`Vcenter.VM_list` is `GET /api/vcenter/vm`. Both send:

- the captured token in exactly `vmware-api-session-id`;
- `Accept: application/json`;
- no `Authorization`, no `Content-Type`, and no request body.

All filter fields are optional. A nil or empty slice is unset and must be
omitted completely—not encoded as `field=`, `field=null`, or an empty JSON
value. Validate the complete filter before I/O: supplied elements must be
unique, nonblank strings. VM power states are limited to `POWERED_OFF`,
`POWERED_ON`, and `SUSPENDED`.

Encode populated arrays with OpenAPI form/explode semantics: repeat one
`name=value` pair per item. Preserve each filter type's declaration order and
the caller's element order. Percent-encode UTF-8 values using RFC 3986 query
encoding (`%20`, never `+`, for a space).

Require exactly HTTP 200 and validate the required summary fields and VM power
state. Optional `cpu_count` and `memory_size_mib` may be absent or JSON null;
when present, each must be an integer. Return fresh slices sorted
deterministically by `(name, datacenter)` for datacenters and `(name, vm)` for
VMs.

## Expiry, replay, and retained work

For an authenticated list operation only, HTTP 401 means the captured session
may have expired. Create a replacement session and replay that same GET
exactly once. If another goroutine has already replaced the failed token, reuse
the published replacement rather than logging in again. Do not retry any other
status, do not refresh more than once for one list call, and never replay a
successful request.

`CollectInventory` lists datacenters first and VMs second. If the VM request
receives 401, refresh and resume the VM request without repeating the completed
datacenter operation. If the VM stage ultimately fails, return the already
collected datacenters in the snapshot alongside the error.

`APIError` represents HTTP and transport failures and preserves the operation
ID, integer HTTP status or zero for transport failure, and a decoded JSON
payload when available. `ProtocolError` represents a successful response that
violates the focused contract. Error text must not reveal credentials, session
tokens, response payloads, response bytes, or underlying transport error text.
Always close response bodies.

Add table-driven tests covering filter validation and both unset and populated
wire shapes. The protected verifier adds further contract, expiry,
concurrency, retained-work, and exact request-log checks. Run:

```sh
go test -race ./...
```
