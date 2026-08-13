# SDDC Manager host commissioning — precheck gate

A Go module that commissions ESXi hosts through the VMware Cloud Foundation 9.0
SDDC Manager REST API, gating the mutating call behind a precheck.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The wire contract, derived from the 9.0 SDDC Manager OpenAPI specification. |
| `docs/official_sources.json` | Where the contract came from: spec path, tag, commit sha, operationIds. |
| `contract.go` | Loads the embedded contract (`package hostcommission`). |
| `mockserver/` | Loopback SDDC Manager stand-in, pinned to the contract, with a request log. |
| `commission/` | The client that runs the workflow. **This is the package to implement.** |

## The workflow

1. `POST /v1/hosts/prechecks` — submit the hosts for prechecking.
2. `GET /v1/hosts/prechecks/{id}` — read the status until it reports completion.
3. `POST /v1/hosts` — commission the hosts, **only** if the completed precheck
   result is `SUCCEEDED`.

If the precheck fails, step 3 must not happen at all, so that nothing is
changed.

## Running the tests

```sh
go test -race ./...
```

The mock listens on loopback. No VMware endpoint is contacted.
