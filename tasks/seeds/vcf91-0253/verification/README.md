# VCF Operations resource inventory

A PowerShell module that takes resource inventory snapshots from VMware Cloud
Foundation Operations 9.1, built on the `VMware.Sdk.Vcf.Ops` PowerCLI module.

The snapshots feed a nightly capacity report and a drift diff against the
previous night, so two things matter more than anything else: a snapshot has to
contain the whole inventory, and two snapshots of the same inventory have to come
out byte-identical no matter which host produced them.

## Layout

| Path | What it is |
| --- | --- |
| `src/VcfOpsInventory/` | The module. `Get-VcfOpsResourceInventory` is the entry point. |
| `docs/contract.json` | The API operations this module may call, plus the pagination and ordering rules, derived from the VCF Operations OpenAPI document. |
| `docs/official_sources.json` | Where `contract.json` came from: repository, commit, spec path, per-operation spec pointers. |
| `tools/mock/` | A loopback mock of the suite-api, pinned to `contract.json`. Serves only the operations the contract names. |
| `tests/Verify.ps1` | Runs the module against the mock and asserts the snapshot and the wire shape of every request. |

## Prerequisite

`VMware.Sdk.Vcf.Ops` must be importable. It is installed by the environment and
is deliberately not vendored into this repository:

```powershell
Install-Module VMware.Sdk.Vcf.Ops -RequiredVersion 13.5.0.25380678 -Scope AllUsers -Force
```

## Running the checks

```powershell
pwsh -NoProfile -File tests/Verify.ps1
```

The verifier starts the mock on a free loopback port, drives three snapshots
through `tests/harness/Invoke-InventoryRun.ps1`, and asserts against the mock's
request log. The third snapshot covers an empty result together with bound zero
and empty-string filters. It contacts no VMware endpoint.

## The API contract

`docs/contract.json` names exactly three operations, each traceable to a pointer
into the specification revision recorded in `docs/official_sources.json`:

| operationId | Method | Path | Issued by |
| --- | --- | --- | --- |
| `acquireToken` | POST | `/suite-api/api/auth/token/acquire` | `Connect-VcfOpsServer` |
| `getCurrentVersionOfServer` | GET | `/suite-api/api/versions/current` | `Connect-VcfOpsServer` |
| `getResources` | GET | `/suite-api/api/resources` | this module |

Requests authenticate with `Authorization: OpsToken <token>`. The caller opens
the connection and the SDK signs from it; the module issues `getResources` and
nothing else.

`contract.json` also records two rules that the specification implies but does
not spell out in one place, and that the mock implements:

- **Pagination.** `page` is a page ordinal starting at 0, not a row offset. The
  collection is exhausted when a response carries no link whose `rel` is `NEXT`.
  Neither `pageInfo.totalCount` nor the length of `resourceList` says anything
  reliable about whether more pages follow.
- **Ordering.** `getResources` declares no sort, so the order is the client's to
  impose: ascending by `resourceKey.name`, ties broken by `identifier`, compared
  ordinally rather than by the current culture.

## `Get-VcfOpsResourceInventory`

```powershell
Get-VcfOpsResourceInventory
    -Server           <VcfOpsServer>  # connection handle from Connect-VcfOpsServer
    [-PageSize        <Int32>]        # default 1000
    [-Name            <String[]>]
    [-AdapterKind     <String[]>]
    [-ResourceKind    <String[]>]
    [-ResourceHealth  <String[]>]
    [-CreatedAfter    <Int64>]        # seconds since 1970-01-01T00:00:00Z
    [-PropertyName    <String>]
    [-PropertyValue   <String>]
```

Every filter is optional, and every filter the caller leaves out is a filter the
server must never see.

Returns one object:

| Property | Type | Meaning |
| --- | --- | --- |
| `Resources` | `object[]` | Every resource in the collection, once each, in the contract's order. |
| `ResourceCount` | `int` | Length of `Resources`. |
| `PagesFetched` | `int` | How many `getResources` requests the snapshot took. |
| `DuplicatesDropped` | `int` | Rows discarded because the same identifier had already been seen. |
| `ReportedTotalCount` | `int` | `pageInfo.totalCount` as the first page reported it, recorded for the caller. It is routinely not equal to `ResourceCount`. |

Each entry in `Resources` carries `Identifier`, `Name`, `AdapterKind`,
`ResourceKind`, `Health` and `CreationTime`.
