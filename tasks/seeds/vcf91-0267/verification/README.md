# VCF Operations report client

A Go module for driving the asynchronous report-generation flow on a VMware
Cloud Foundation Operations 9.1 appliance.

## Layout

| Path                      | Role                                                                     |
| ------------------------- | ------------------------------------------------------------------------ |
| `docs/contract.json`      | Wire contract derived from the VCF Operations OpenAPI specification       |
| `docs/official_sources.json` | Provenance: spec path, commit sha, and the operationIds extracted     |
| `internal/contract`       | Loader for `docs/contract.json`                                          |
| `internal/mockops`        | In-process mock appliance pinned to the contract, with a request log      |
| `reportclient`            | The client package (stub — implement this, with tests)                    |
| `verify/`                 | Protected wire-shape verification. Do not edit.                           |

## The flow

Four operations, all named in `docs/contract.json`:

1. `acquireToken` — `POST /suite-api/api/auth/token/acquire`
2. `createReport` — `POST /suite-api/api/reports`
3. `getReport` — `GET /suite-api/api/reports/{id}` (poll until terminal)
4. `downloadReport` — `GET /suite-api/api/reports/{id}/download`

`createReport` returns as soon as the request is queued. Its response status is
`QUEUED`, never the outcome, so the report must be polled with `getReport` until
`status` is one of `contract.reportStatus.terminal` before anything else happens.

## The mock

`internal/mockops` builds its routing table from `docs/contract.json` and serves
only the four operations the contract names — everything else answers 404 and is
recorded with an empty `OperationID`. Every request is logged in arrival order
and readable with `srv.Requests()`, which returns a copy and is safe under
`-race`.

```go
srv := mockops.Start(t, mockops.Scenario{PollStatuses: []string{"QUEUED", "RUNNING", "COMPLETED"}})
sc := srv.Scenario() // credentials, token, report id, download body — defaults applied
// ... configure BaseURL: srv.URL(), HTTPClient: srv.Client() ...
for _, r := range srv.Requests() {
    fmt.Println(r.Index, r.OperationID, r.Method, r.Path, r.RawQuery, string(r.Body))
}
```

`Scenario.PollStatuses` drives successive `getReport` responses; the last entry
repeats if polling continues past the end of the slice. `downloadReport` answers
409 until the most recent poll reported the successful terminal status, so the
mock cannot be satisfied by skipping the polling step.

## Running

```
./verify.sh          # gofmt, go vet, go test -race ./...
go test -race ./...  # tests only
```
