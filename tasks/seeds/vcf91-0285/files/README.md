# VCF Operations for Networks application inventory

Implement `Connect-VcfOnServer` and `Get-VcfOnApplication` in
`src/VcfOpsNetworks/VcfOpsNetworks.psm1`.

VCF Operations for Networks is the VCF 9.1 successor to vRealize Network
Insight. Its REST surface is not covered by a generated PowerCLI cmdlet set, so
this module is a companion that drives the same OpenAPI transport the
`VMware.Sdk.Vcf` PowerCLI modules use (`VMware.OpenAPI`, assembly
`VMware.Binding.OpenApi`). The prerequisite modules are installed by the runner
and are intentionally not vendored into this repository.

`docs/contract.json` is a focused wire-contract projection of the VCF
Operations for Networks 9.1 OpenAPI specification pinned by
`docs/official_sources.json`. It is the authoritative description of the three
operations in scope:

| operationId          | method | path                                |
| -------------------- | ------ | ----------------------------------- |
| `create`             | POST   | `/api/ni/auth/token`                |
| `listApplications`   | GET    | `/api/ni/groups/applications`       |
| `getApplicationById` | GET    | `/api/ni/groups/applications/{id}`  |

The transport helpers at the top of the module (`New-NiApiConnection`,
`New-NiRequestOptions`, `Invoke-NiRequest`, `Get-NiField`) are already written.
They add nothing to the wire on their own: every header, query parameter and
body field comes from the `RequestOptions` object you populate.

Run the protected verifier with:

    python3 -B tests/verify.py

The verifier starts a contract-pinned loopback mock, drives the module through
`tests/exercise.ps1`, and asserts the exact request wire shape recorded by the
mock. No live VMware endpoint is contacted.
