# VCF Operations maintenance window module

Declarative maintenance window management for VMware Cloud Foundation Operations
9.1, built on the VMware.Sdk.Vcf.Ops PowerCLI module.

The fleet maintenance orchestrator re-runs a plan from the top whenever a step is
interrupted, so every step it calls has to be safe to repeat. `Set-VcfOpsMaintenanceWindow`
is the step that puts a maintenance schedule in place.

## Layout

| Path | Purpose |
| --- | --- |
| `src/VcfOps.MaintenanceWindow/VcfOps.MaintenanceWindow.psd1` | Module manifest. Pins the exported function and the SDK dependency. |
| `src/VcfOps.MaintenanceWindow/VcfOps.MaintenanceWindow.psm1` | Implementation. |
| `docs/contract.json` | Authoritative wire projection of the operations this module uses. |
| `docs/official_sources.json` | Upstream provenance for `docs/contract.json`. |
| `tests/mock_vcf_operations.py` | Loopback VCF Operations mock, pinned to `docs/contract.json`. |
| `tests/exercise.ps1` | Harness that opens a real SDK session against the mock. |
| `tests/verify.py` | Verifier. |

## Contract

`docs/contract.json` is derived from
`specifications/vcf-operations/vcf-operations-openapi.json` in
[vmware/vcf-api-specs](https://github.com/vmware/vcf-api-specs) (Apache-2.0), pinned
at commit `c3f3b52c845dd967cabbc21680e893292077d5ba` ("VCF API Specs 9.1 release
readiness"). It projects five `operationId`s:

| operationId | Method | Path |
| --- | --- | --- |
| `acquireToken` | POST | `/suite-api/api/auth/token/acquire` |
| `getCurrentVersionOfServer` | GET | `/suite-api/api/versions/current` |
| `getMaintenanceSchedules` | GET | `/suite-api/api/maintenanceschedules` |
| `createMaintenanceSchedules` | POST | `/suite-api/api/maintenanceschedules` |
| `updateMaintenanceSchedules` | PUT | `/suite-api/api/maintenanceschedules` |

The first two belong to `Connect-VcfOpsServer`; the module itself only reaches the
last three.

## Prerequisites

`VMware.Sdk.Vcf.Ops` 13.5.0.25380678 is installed by the environment. It is not
vendored into this repository and must not be replaced, wrapped, or intercepted.

## Running the tests

```sh
python3 -B tests/verify.py
```

The verifier starts a fresh loopback mock per scenario on 127.0.0.1 and never
contacts a live VMware endpoint.
