# VCF Operations — precheck-gated adapter instance onboarding

A PowerShell module that onboards a VMware Cloud Foundation Operations 9.1 adapter
instance only after the platform's own connection precheck succeeds, so a failed
precheck leaves the target untouched.

## Layout

| Path | Role |
| --- | --- |
| `src/VcfOps.AdapterOnboarding/VcfOps.AdapterOnboarding.psm1` | the module implementation — the only file to edit |
| `src/VcfOps.AdapterOnboarding/VcfOps.AdapterOnboarding.psd1` | module manifest, declares the SDK dependency |
| `docs/contract.json` | focused projection of the pinned VCF Operations OpenAPI document |
| `docs/official_sources.json` | provenance for the projection: repository, commit, spec path, per-operation pointers |
| `tools/contractmock/Start-VcfOpsContractMock.ps1` | loopback mock; serves only the contract's operations and writes a request log |
| `tests/Invoke-Verification.ps1` | verifier; asserts the exact request wire shape |
| `tests/Invoke-Scenario.ps1` | runs one scenario in an isolated child process |

## Dependency

`VMware.Sdk.Vcf.Ops` version `13.5.0.25380678` (the VCF Operations PowerCLI SDK
module) is installed in the environment. It is not vendored here and must not be
reimplemented.

## Verification

```sh
pwsh -NoProfile -File tests/Invoke-Verification.ps1
```

The verifier starts the contract mock on an ephemeral `127.0.0.1` port and reads
its request log. No live VMware endpoint is contacted.
