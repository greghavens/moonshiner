# Asynchronous vCenter clone client

Implement package `example.com/vcfasync/vcenter` for the VCF 9.1 vSphere
Automation API using only the Go standard library.

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

Use the JSON names documented by the API. Optional fields must use omission
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
