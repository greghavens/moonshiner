# VcfOpsReporting

A PowerShell module that drives on-demand **report generation** against the
VMware Cloud Foundation Operations API (VCF 9.1), built on the `VMware.Sdk.Vcf.Ops`
PowerCLI SDK.

Report generation is an **asynchronous** operation. `createReport` returns as soon as the
request is accepted, with a status that is not yet terminal. The report can only be
downloaded once its status has reached a terminal state, so the module has to poll for it —
never assume the report is ready because the create call returned 200.

```
createReport  ->  getReport (poll) ... getReport  ->  downloadReport
   QUEUED           QUEUED    RUNNING     COMPLETED        CSV / PDF
```

## Layout

| Path | What it is |
| --- | --- |
| `src/VcfOpsReporting/` | The module. Five public functions. |
| `docs/contract.json` | The wire contract. Authoritative. |
| `docs/official_sources.json` | Where the contract came from. |
| `tools/mock/Start-VcfOpsMock.ps1` | Loopback mock of the API, pinned to the contract. |
| `tests/Invoke-Verification.ps1` | Verification. Asserts the exact wire shape. |

## The contract

`docs/contract.json` is derived from the `vcf-operations` OpenAPI document in
[`vmware/vcf-api-specs`](https://github.com/vmware/vcf-api-specs) (Apache-2.0), pinned to the
commit recorded in `docs/official_sources.json`. It is generated from the specification
document itself, not from a rendered documentation page.

It names five operations, and those are the only five the mock will serve:

| operationId | | |
| --- | --- | --- |
| `acquireToken` | `POST` | `/suite-api/api/auth/token/acquire` |
| `getCurrentVersionOfServer` | `GET` | `/suite-api/api/versions/current` |
| `createReport` | `POST` | `/suite-api/api/reports` |
| `getReport` | `GET` | `/suite-api/api/reports/{id}` |
| `downloadReport` | `GET` | `/suite-api/api/reports/{id}/download` |

Two things in the contract are **not** facts of the specification and are labelled as such
under `auth.taskDefined` and `taskDefined`:

* The `Authorization` header value format. The document declares the header but not the token
  prefix; `OpsToken {token}` is what the SDK actually sends.
* The terminal/non-terminal partition of `report.status`. That property is a free-form string
  in the specification with no `enum`, so the partition is this project's decision:
  non-terminal `QUEUED` / `SCHEDULED` / `RUNNING`, terminal-success `COMPLETED`,
  terminal-failure `FAILED` / `ABORTED`.

`docs/contract.json` and `docs/official_sources.json` are inputs, not outputs. Do not edit them.

## Only send what the caller asked for

Both the mock and the verification are strict about this: a property or query parameter the
caller did not supply must be **absent from the request**, not present as `null`, `""`, or `[]`.

This is about the requests the module composes. The session handshake is composed by
`Connect-VcfOpsServer` itself, which serializes `authSource` either way; what you decide there
is whether a value goes into it.

```
Start-VcfOpsReportGeneration -Session $s -ReportDefinitionId $d -ResourceId $r
  => {"reportDefinitionId":"...","resourceId":"..."}          # and nothing else

Save-VcfOpsReport -Session $s -ReportId $id -Path out.csv
  => GET /suite-api/api/reports/<id>/download                 # no query string at all

Save-VcfOpsReport -Session $s -ReportId $id -Path out.csv -Format CSV
  => GET /suite-api/api/reports/<id>/download?format=CSV
```

This applies to nested models too: a `traversalSpec` built from a name alone must serialise to
`{"name":"..."}` and must not carry the other optional `traversal-spec` properties.

## The SDK

`VMware.Sdk.Vcf.Ops` is installed by the environment as a prerequisite. It is **never** vendored
into this repository, and the verification fails if it is.

Useful pieces of it:

* `Connect-VcfOpsServer` — authenticates and returns a server object carrying `ServiceUri` and
  `SessionSecret`.
* `Get-VcfOpsOperation -Name <operationId>` — resolves an operationId to its HTTP method and path
  template, straight from the SDK's own operation table.
* `Initialize-VcfOps<model>` — builds request models.
* `Invoke-VcfOps<Operation> -AsInvokeRestMethodRequest` — returns the request the SDK *would*
  send (`Uri`, `Method`, `Body`, `ContentType`, `Headers`) instead of sending it. The `Body` it
  produces is already correct about omitting properties that were never set. Note the cmdlet
  emits a collection whose first element is `$null`.

**Known constraint.** Do not rely on the SDK's typed wrappers to render `{id}` path segments.
Their path-parameter serializer reflects over the public properties of the value instead of
calling `ToString()`, and on current .NET releases `System.Guid` has public instance properties,
so a report id can come out on the wire as `Variant,4,Version,3` instead of the identifier.
Build `{id}` paths from the template returned by `Get-VcfOpsOperation`. The verification asserts
the identifier appears in the path verbatim.

The SDK also joins its service URI and base path into a `//suite-api/...` double slash. The mock
normalises repeated slashes before routing, so this is not something you need to work around.

## Running it

```bash
pwsh -File tests/Invoke-Verification.ps1
```

The verification starts the mock on an ephemeral loopback port for each scenario, exercises the
module against it, and asserts the request log the mock wrote. It covers the successful run, a
run supplying optional fields, a run whose report ends `FAILED`, and a report that never becomes
terminal. **No VMware endpoint is contacted** — the only server involved is `127.0.0.1`.

To poke at the mock by hand:

```bash
pwsh -File tools/mock/Start-VcfOpsMock.ps1 -StateDir /tmp/opsmock
# /tmp/opsmock/port         the port it bound
# /tmp/opsmock/requests.jsonl   one JSON object per request received
```
