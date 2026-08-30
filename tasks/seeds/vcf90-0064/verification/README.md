# VCF Operations maintenance-window reconciler

A nightly pipeline reconciles the maintenance windows of a VMware Cloud
Foundation 9.0 estate. The pipeline retries, so the same invocation has to be
safe to run again without leaving a second schedule behind.

## Layout

```
docs/contract.json            the operations this module may call, projected
                              from the pinned VCF Operations OpenAPI document
docs/official_sources.json    where that projection came from: repository, tag,
                              commit sha, spec path and every operationId
module/VcfOpsMaintenanceWindow/
    VcfOpsMaintenanceWindow.psd1
    VcfOpsMaintenanceWindow.psm1    <- the only file to edit
verification/Invoke-Verification.ps1  acceptance harness
verification/contract_mock.py         loopback VCF Operations service
```

## Running the harness

```
pwsh -NoProfile -File verification/Invoke-Verification.ps1
```

It starts `contract_mock.py` on `127.0.0.1` on an ephemeral port, connects the
genuine `VMware.Sdk.Vcf.Ops` cmdlets to it with dummy credentials, drives the
module, and then reads the mock's JSON Lines request log and asserts the exact
shape of every request that was made. It contacts no VMware endpoint.

The mock is a real little service. It stores what you create, assigns the
identifiers, and enforces the uniqueness rule the specification states for
`createMaintenanceSchedules`:

```
422 - A maintenance schedule with the same key already exists
```

Nothing is pre-seeded; every schedule it knows about got there because the
module put it there.

## Prerequisites

`pwsh` 7.2+, `python3`, and the `VMware.Sdk.Vcf.Ops` PowerCLI module, which the
environment installs. It is never vendored into this repository.

## Protected

`docs/` and `verification/` are protected. Everything under them, including the
contract projection and its provenance record, is part of the acceptance
harness. Edit only
`module/VcfOpsMaintenanceWindow/VcfOpsMaintenanceWindow.psm1`.
