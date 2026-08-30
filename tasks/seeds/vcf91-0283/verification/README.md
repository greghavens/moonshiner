# VcfOpsNetworks.Applications

A small PowerShell module that bulk-saves discovered applications in **VCF
Operations for Networks** (VMware Cloud Foundation 9.1, the successor to
vRealize Network Insight) and waits for the resulting asynchronous task to
finish.

## Layout

```
docs/contract.json          Projection of the four API operations this module uses
docs/official_sources.json  Pinned provenance of that projection
src/VcfOpsNetworks.Applications/
    VcfOpsNetworks.Applications.psd1   Module manifest
    VcfOpsNetworks.Applications.psm1   Implementation
tests/mock_vcf_ops_networks.py         Loopback mock, pinned to docs/contract.json
tests/exercise.ps1                     Drives the module against the mock
tests/verify.py                        Deterministic verification
```

## Prerequisite

The `VMware.Sdk.Vcf.Ops` PowerCLI module (13.5.0.25380678 or later) is installed
by the environment. It is **not** vendored into this repository and must not be.
Its shared OpenAPI binding layer (module `VMware.OpenAPI`, assembly
`VMware.Binding.OpenApi`) supplies the HTTP client this module uses:

| Type | Role |
| --- | --- |
| `VMware.Binding.OpenApi.Client.Configuration` | Base path and client settings |
| `VMware.Binding.OpenApi.Client.ApiClient` | Issues the request |
| `VMware.Binding.OpenApi.Client.RequestOptions` | Path, query, header and body of one request |
| `VMware.Binding.OpenApi.Client.ApiResponse[T]` | `StatusCode`, `Data`, `RawContent`, `ErrorText` |

`New-NiApiConnection`, `New-NiRequestOptions` and `Invoke-NiRequest` in the
module wrap those types and are already written. They add nothing to the
request on their own, so the wire shape is decided entirely by the
`RequestOptions` instance handed to `Invoke-NiRequest`:

```powershell
$connection = New-NiApiConnection -Server 'https://vcfon.example.com'

$options = New-NiRequestOptions
$options.HeaderParameters.Add('Authorization', "NetworkInsight $token")
$options.QueryParameters.Add('discovery_type', 'FLOW_BASED_DISCOVERY')
$response = Invoke-NiRequest -Connection $connection -Method GET `
    -Path '/groups/discovered-applications' -Options $options

if ($response.StatusCode -ne 200) { throw "..." }   # the client does not throw
$response.Data.results
```

Two behaviours of this client are worth knowing before you start:

* It **does not raise** on a non-success HTTP status. `Invoke-NiRequest` returns
  the response with `StatusCode` set and it is the caller's job to check it.
* `RequestOptions.Data` is serialized verbatim. An `[ordered]@{}` produces
  exactly the keys it contains, in order, and nothing else — so a field is
  omitted from the JSON body simply by never adding it.

## The API contract

`docs/contract.json` is the authoritative projection of the four operations
used here. It is derived from the pinned VCF Operations for Networks OpenAPI
specification in the `vmware/vcf-api-specs` repository (Apache-2.0); the exact
file path, revision and operation ids are recorded in
`docs/official_sources.json`. Where a path-level `example` in the specification
disagrees with the referenced component schema, the component schema wins.

## Running the verification

```
python3 -B tests/verify.py
```

The verifier starts a contract-pinned mock on `127.0.0.1` for each scenario,
runs the module once through `tests/exercise.ps1`, and then asserts the exact
wire shape of every request the mock recorded. No live VMware endpoint is
contacted.
