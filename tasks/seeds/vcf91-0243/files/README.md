# VcfVsanDp

PowerShell bindings for the VMware Cloud Foundation 9.1 **vSAN Data Protection**
(snapservice) API.

The `VMware.Sdk.Vcf` PowerCLI family (`SddcManager`, `Installer`, `CloudBuilder`,
`Ops`) ships no binding for the snapservice API exposed by the Snapshot
Appliance, so this module fills that gap. The SDK is installed in this
environment as a prerequisite and is **never vendored into this repository**.

## Layout

| Path | Purpose |
| --- | --- |
| `src/VcfVsanDp/` | The module. This is the only thing you edit. |
| `docs/contract.json` | The wire contract, derived from the published OpenAPI spec. |
| `docs/official_sources.json` | Provenance: spec repository, commit sha, operation ids. |
| `mock/snapservice_mock.py` | Loopback appliance mock. Serves only the four contracted operations. |
| `mock/fixture.json` | The data the mock serves, plus its credentials. |
| `tests/drive.ps1` | Fixed harness. Defines the public surface the module must expose. |
| `tests/verify.py` | Checks the module's output *and* the exact wire shape of its requests. |

## Running the checks

```sh
python3 tests/verify.py
```

The verifier starts the mock on an ephemeral loopback port, runs `tests/drive.ps1`
against it, and then inspects `mock/requests.jsonl`. No live VMware endpoint is
contacted.

## The contract

`docs/contract.json` names four operation ids from the specification:

- `Snapservice.Sessions_create`
- `Snapservice.Sessions_delete`
- `Snapservice.Clusters.ProtectionGroups_list`
- `Snapservice.Reports.Clusters.ProtectionGroups.Snapshots_list`

Read its `serializationRules` block carefully. The snapshot report operation
takes two object-typed query parameters (`filter` and `iterate`) that are
`style: form, explode: true`, which changes what actually appears on the wire.
