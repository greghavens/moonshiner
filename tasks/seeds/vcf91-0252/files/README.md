# VCF Operations alert triage

A PowerShell module that runs the nightly alert triage pass against
VMware Cloud Foundation Operations 9.1, built on the `VMware.Sdk.Vcf.Ops`
PowerCLI module.

## Layout

| Path | What it is |
| --- | --- |
| `src/VcfOpsAlertTriage/` | The module. `Invoke-VcfOpsAlertTriage` is the entry point. |
| `docs/contract.json` | The five API operations this module may call, derived from the VCF Operations OpenAPI document. |
| `docs/official_sources.json` | Where `contract.json` came from: repository, commit, spec path, per-operation spec pointers. |
| `tools/mock/` | A loopback mock of the suite-api, pinned to `contract.json`. Serves only the operations the contract names. |
| `tests/Verify.ps1` | Runs the module against the mock and asserts the wire shape of every request. |

## Prerequisite

`VMware.Sdk.Vcf.Ops` must be importable. It is installed by the environment
and is deliberately not vendored into this repository:

```powershell
Install-Module VMware.Sdk.Vcf.Ops -RequiredVersion 13.5.0.25380678 -Scope CurrentUser -Force
```

## Running the checks

```powershell
pwsh -NoProfile -File tests/Verify.ps1
```

The verifier starts the mock on a free loopback port, drives two triage runs
through `tests/harness/Invoke-TriageRun.ps1`, and asserts against the mock's
request log. It contacts no VMware endpoint.

## The API contract

`docs/contract.json` names exactly five operations, each traceable to a pointer
into the specification revision recorded in `docs/official_sources.json`:

| operationId | Method | Path |
| --- | --- | --- |
| `getCurrentVersionOfServer` | GET | `/suite-api/api/versions/current` |
| `acquireToken` | POST | `/suite-api/api/auth/token/acquire` |
| `queryAlert` | POST | `/suite-api/api/alerts/query` |
| `modifyAlerts` | POST | `/suite-api/api/alerts` |
| `releaseToken` | POST | `/suite-api/api/auth/token/release` |

Requests authenticate with `Authorization: OpsToken <token>`.

`getCurrentVersionOfServer` is the probe `Connect-VcfOpsServer` issues while
establishing the connection; the module itself uses the other four.

## `Invoke-VcfOpsAlertTriage`

```powershell
Invoke-VcfOpsAlertTriage
    -Server            <VcfOpsServer>  # connection handle from Connect-VcfOpsServer
    -Credential        <PSCredential>  # the credentials this run authenticates with
    -Action            <String>        # modifyAlerts action applied to each alert
    [-AuthSource       <String>]
    [-SuspendMinutes   <Int32>]
    [-OwnerAccountId   <String>]
    [-PageSize         <Int32>]        # default 100
    [-ActiveOnly]
    [-AlertCriticality <String[]>]
    [-ResourceKind     <String>]
```

The caller owns the connection; the run owns its own token. The caller's
connection is a request factory, not a credential the run may spend.

Returns one object:

| Property | Type | Meaning |
| --- | --- | --- |
| `TokensAcquired` | `int` | How many tokens the run acquired, including refreshes. |
| `AlertsFound` | `int` | Alerts matched by the query, across all pages. |
| `AlertsActioned` | `int` | Alerts the action was successfully applied to. |
| `ActionedAlertIds` | `string[]` | The alert ids actioned, in the order they were actioned. |
