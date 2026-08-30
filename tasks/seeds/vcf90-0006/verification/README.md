# vcf-host-commission

Platform-engineering tooling for VMware Cloud Foundation 9.0. This repository holds a
PowerShell module that talks to the SDDC Manager REST API through the
`VMware.Sdk.Vcf` PowerCLI SDK, plus the offline harness we use to prove what the
module puts on the wire.

## Layout

| Path | Purpose |
| --- | --- |
| `docs/` | The spec-derived API contract and the record of where it came from. |
| `src/VcfHostCommission/` | The PowerShell module. |
| `tools/` | The loopback mock of SDDC Manager used by the tests. |
| `tests/` | Protected verification. Read it, run it, but do not edit it. |
| `scripts/` | Environment prerequisites. |

## Prerequisites

`scripts/Install-Prerequisites.ps1` installs `VMware.Sdk.Vcf.SddcManager` from the
PowerShell Gallery. The SDK is an environment prerequisite: it is never vendored
into this repository, and `src/` must not contain a copy of it.

## Running the checks

```
pwsh -NoProfile -File tests/Verify-Contract.ps1
```

The verification is fully offline. It starts the mock in `tools/` on a loopback
port and reads the mock's request log; it never contacts a live VMware endpoint.

## House rules

* Anything under `tests/` is protected. Changing it is not a fix.
* No VMware SDK sources, DLLs, or nupkgs are committed to this repository.
* `docs/contract.json` is derived from the published OpenAPI specification, and
  `docs/official_sources.json` says exactly which revision of it.
