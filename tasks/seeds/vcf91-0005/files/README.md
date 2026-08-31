# VCF lifecycle connectivity change

Implement `src/VcfLifecycleConnectivity.psm1` and export one function:

```powershell
Invoke-VcfLifecycleConnectivityChange `
  -Server $connection `
  -ProxyHost 'proxy.edge.example.test' `
  -ProxyPort 8443 `
  -ProxyProtocol HTTPS `
  -ServiceName 'VCF Depot' `
  -ServiceType 'VCF_DEPOT' `
  -ServiceKey 'vcf-depot' `
  -NodeName 'depot-1' `
  -AddressType 'FQDN' `
  -AddressValue 'depot.example.test'
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
3. Build a `ServicesConfig` containing exactly one service, one node, and one
   address, then call operation `updateServicesConfig`.

Construct request models with the VMware SDK initializers. In particular, do
not populate `ProxyConfiguration.isConfigured`, `username`, `password`, or
`isAuthenticated`; do not populate service `version` or node `port`, `baseUrl`,
or `certificates`. Unset optional properties must be omitted on the JSON wire.
Sending them as null, false, empty strings, arrays, or objects is incorrect.

## Result contract

Return one object and do not throw for an API failure that can be reported. It
must have this shape:

```text
Outcome : Succeeded | PartialFailure | Failed
Steps   : ordered array of step reports
```

Each step report has these properties:

- `Name`: `Proxy` or `ServicesConfig`
- `OperationId`: the exact OpenAPI operationId for the mutation
- `Status`: `Succeeded`, `Failed`, or `NotRun`
- `TaskId`: the task ID when an operation returned one, otherwise `$null`
- `TaskStatus`: the observed terminal task status, otherwise `$null`
- `ErrorCode`: the structured VCF error code when available, otherwise `$null`
- `ErrorMessage`: the VCF or PowerShell error text when available, otherwise
  `$null`

The acceptance scenario returns an in-progress proxy task, a successful result
from `getTask`, and then rejects the external-services configuration with
`SERVICES_CONFIG_SCHEMA_VALIDATION_FAILED`. The required overall result is
`PartialFailure`; the successful proxy report must remain successful and retain
its task ID/status.

Use `src/VcfLifecycleConnectivity.psd1` as the import entry point. Keep
`VMware.Sdk.Vcf.SddcManager` as a required module and export only
`Invoke-VcfLifecycleConnectivityChange`.

Run the protected acceptance check with:

```bash
python3 -B tests/verify.py
```

Everything under `tests/` and `docs/`, plus
`src/VcfLifecycleConnectivity.psd1`, is protected. Edit only
`src/VcfLifecycleConnectivity.psm1`.
