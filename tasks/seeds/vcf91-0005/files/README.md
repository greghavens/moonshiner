# VCF lifecycle connectivity change

Implement `src/VcfLifecycleConnectivity.psm1` and export one function:

```powershell
Invoke-VcfLifecycleConnectivityChange `
  -Server $connection `
  -ProxyHost 'proxy.edge.example.test' `
  -ProxyPort 8443 `
  -ProxyProtocol HTTPS `
  -DepotDownloadToken $token
```

`$connection` is an existing `VcfSddcManagerServer` produced by
`Connect-VcfSddcManagerServer`. The function must use the generated commands from
the environment-provided `VMware.Sdk.Vcf.SddcManager` 13.5.0.25380678 module
from VCF PowerCLI 9.1. Do not install, copy, generate, vendor, wrap, or replace
the SDK, and do not use
`Invoke-RestMethod`, `Invoke-WebRequest`, `HttpClient`, `curl`, or another raw
HTTP client.

## Required workflow

Perform the steps in this exact order:

1. Build a `ProxyConfiguration` with only `isEnabled`, `host`, `port`, and
   `transferProtocol`, then call OpenAPI operation `updateProxyConfiguration`.
2. Retrieve the returned task once through operation `getTask`. Only the
   terminal status `SUCCESSFUL` makes the step successful.
3. Build a `DepotSettings` containing only
   `vmwareAccount.downloadToken`, then call operation `updateDepotSettings`.

Construct request models with the VMware SDK initializers. In particular, do
not populate `ProxyConfiguration.isConfigured`, `username`, `password`, or
`isAuthenticated`; do not populate the other `DepotAccount` or `DepotSettings`
properties. Unset optional properties must be omitted on the JSON wire. Sending
them as null, false, empty strings, arrays, or objects is incorrect.

## Result contract

Return one object and do not throw for an API failure that can be reported. It
must have this shape:

```text
Outcome : Succeeded | PartialFailure | Failed
Steps   : ordered array of step reports
```

Each step report has these properties:

- `Name`: `Proxy` or `Depot`
- `OperationId`: the exact OpenAPI operationId for the mutation
- `Status`: `Succeeded`, `Failed`, or `NotRun`
- `TaskId`: the task ID when an operation returned one, otherwise `$null`
- `TaskStatus`: the observed terminal task status, otherwise `$null`
- `ErrorCode`: the structured VCF error code when available, otherwise `$null`
- `ErrorMessage`: the VCF or PowerShell error text when available, otherwise
  `$null`

The deterministic acceptance scenario returns an in-progress proxy task, a
successful result from `getTask`, and then rejects the depot token with
`DEPOT_TOKEN_REJECTED`. The required overall result is `PartialFailure`; the
successful proxy report must remain successful and retain its task ID/status.

Use `src/VcfLifecycleConnectivity.psd1` as the import entry point. Keep
`VMware.Sdk.Vcf.SddcManager` as a required module and export only
`Invoke-VcfLifecycleConnectivityChange`.

## Contract provenance

[`docs/contract.json`](docs/contract.json) is a focused contract derived from
the VCF 9.1 SDDC Manager OpenAPI specification, not from an API documentation
page. [`docs/official_sources.json`](docs/official_sources.json) pins the
upstream repository, full commit SHA, specification path, and every operationId
served by the loopback fixture.

Run the protected acceptance check with:

```bash
python3 -B .moonshiner/verify.py
```

It generates credentials and scenario values at runtime, starts only a loopback
HTTP server, and never contacts a VMware endpoint. The genuine SDK connection
performs `createToken` and its non-OpenAPI `GET /v1/sddc-manager` version probe
before the three workflow operations. The fixture permits that bootstrap probe
in addition to the four operationIds pinned by the focused contract.

Everything under `.moonshiner/` and `docs/`, plus
`src/VcfLifecycleConnectivity.psd1`, is protected. Edit only
`src/VcfLifecycleConnectivity.psm1`.
