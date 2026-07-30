# Asynchronous vCenter clone client

Implement package `example.com/vcfasync/vcenter` using only the Go standard
library. The authoritative, reduced VCF 9.1 API contract is
[`docs/contract.json`](docs/contract.json); its provenance is recorded in
[`docs/official_sources.json`](docs/official_sources.json).

The supplied `internal/mockvcenter` package is a loopback-only vCenter fixture.
It serves exactly the two operations named by the contract and exposes a
race-safe request log. No live VMware endpoint is needed or permitted by the
tests.

## Required API

Create package `vcenter` with these exported declarations:

```go
type Client struct { /* internal fields */ }

func NewClient(baseURL, sessionID string, httpClient *http.Client) (*Client, error)

type CloneSpec struct {
    Source                 string
    Name                   string
    Placement              *ClonePlacementSpec
    DisksToRemove          []string
    DisksToUpdate          map[string]DiskCloneSpec
    PowerOn                *bool
    GuestCustomizationSpec *GuestCustomizationSpec
}

type ClonePlacementSpec struct {
    Folder       string
    ResourcePool string
    Host         string
    Cluster      string
    Datastore    string
}

type DiskCloneSpec struct {
    Datastore string
}

type GuestCustomizationSpec struct {
    Name string
}

func (c *Client) CloneAndWait(
    ctx context.Context,
    spec CloneSpec,
    pollInterval time.Duration,
) (string, error)
```

Use the JSON names from the contract. Optional fields must use omission
semantics:

- a nil optional pointer is absent from JSON;
- an empty optional slice or map is absent from JSON;
- empty fields inside an optional placement object are absent;
- `PowerOn` distinguishes unset from an explicitly supplied `false`.

`CloneAndWait` must:

1. issue the task-form clone request with the session and JSON headers;
2. decode the JSON string returned with HTTP 202 as the task identifier;
3. immediately retrieve that task, without sending an empty optional task
   filter;
4. continue polling while its status is `PENDING`, `RUNNING`, or `BLOCKED`;
5. on `SUCCEEDED`, decode the task result as the cloned VM identifier;
6. on `FAILED`, return a useful error; and
7. stop promptly when the context is canceled.

Reject invalid construction parameters and invalid clone specifications.
Return descriptive errors for non-success HTTP responses, malformed responses,
unknown task statuses, and a successful task that lacks a string result.
Always close response bodies.

Add table-driven tests for minimal and populated request shapes and for task
state outcomes. All project tests must be race-safe:

```sh
go test -race ./...
```
