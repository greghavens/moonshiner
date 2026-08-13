# docs/

Two files belong here. Neither is in the repository yet; both are yours to write.

## `contract.json`

The machine-readable contract for the slice of the VCF Automation API this
client speaks. Its shape is defined by the Go types in `../contract`, and
`contract.Load` validates it — read `contract.go` for the rules it enforces,
which are structural only. Whether the *values* are right is a matter of having
read the reference correctly.

It must define exactly these operation IDs, which the client, the mock and the
verifier all address operations by:

| ID | what it does |
| --- | --- |
| `auth.token` | exchanges the long-lived API token for a short-lived access token |
| `deployments.list` | lists deployments, a page at a time |
| `deployments.get` | fetches one deployment by ID |
| `catalog.items.list` | lists catalog items, a page at a time |
| `catalog.items.request` | requests a new deployment from a catalog item |

For each operation record the method, the path template, every path parameter,
every query parameter, the request body (content type and fields) where there
is one, and the success response (content type, whether it is an object or an
array, and its fields). Mark each parameter and field required or optional, and
record documented defaults — the mock applies them, and the client relies on
them.

`source` must state, in prose, that this contract was transcribed from
reference documentation rather than from a published specification, and must
set `specification_available` to `false`. This is not a formality: it is the
one durable signal to a later reader that these field names carry no
machine-checked guarantee behind them.

## `official_sources.json`

The provenance record. Its shape is defined by `../contract/sources.go`.

One record per operation per page consulted, each naming the operation, the
page's URL, the page's own title, and the date it was read as `YYYY-MM-DD`.
Every operation in the contract must be covered by at least one record. If one
page documents several operations, record it once per operation it documents.
