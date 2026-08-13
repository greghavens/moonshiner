# opsnet — vCenter onboarding for VCF Operations for Networks 9.1

A small Go client that registers a vCenter as a data source in VMware Cloud
Foundation Operations for Networks 9.1 (the successor to vRealize Network
Insight).

Onboarding is a two-step flow. The appliance exposes a precheck that validates a
candidate vCenter — reachability, credentials, collector privileges — and a
separate call that actually creates the data source. The precheck exists so that
a bad vCenter never becomes a half-registered data source that an operator has
to clean up by hand.

## Layout

| Path | What it is |
| --- | --- |
| `onboarding/` | the client package |
| `docs/contract.json` | the REST contract, derived from the OpenAPI document |
| `docs/official_sources.json` | where that contract came from: repo, spec path, commit sha, operation IDs |
| `internal/mockni/` | loopback mock of the two contract operations, with a request log |
| `verify/` | contract verification driven against the mock |
| `scripts/verify.sh` | the whole check in one command |

## Running the checks

```
./scripts/verify.sh
```

Everything runs offline against `127.0.0.1`. No live VMware appliance is
contacted at any point.

## Contract

`docs/contract.json` is transcribed from the OpenAPI specification recorded in
`docs/official_sources.json`, not from a rendered API reference page. When the
code and a documentation page disagree, the specification wins.
