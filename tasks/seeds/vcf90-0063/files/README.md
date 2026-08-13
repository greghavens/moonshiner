# VCF Operations 9.0 alert inventory

`src/VcfOpsAlertInventory` is a PowerShell 7 module whose single public function,
`Get-VcfOpsAlertInventory`, must return the **complete** VCF Operations alert collection in a
**stable order**. The manifest and the function's parameter block are already in place; the
function body is not.

## Layout

| Path | Role |
| --- | --- |
| `src/VcfOpsAlertInventory/VcfOpsAlertInventory.psm1` | the only file to edit |
| `src/VcfOpsAlertInventory/VcfOpsAlertInventory.psd1` | manifest; fixes the public surface |
| `docs/contract.json` | the wire contract |
| `docs/official_sources.json` | where the contract came from |
| `.protected/mock_vcf_ops.py` | loopback stand-in for the appliance |
| `.protected/invoke-inventory.ps1` | how the verifier calls your function |
| `.protected/verify.py` | the verifier |
| `run_tests.sh` | runs the verifier |

## Transport

`VMware.Sdk.Vcf.Ops` is installed by the environment and is the only supported client for
these operations. Do not vendor an SDK, add a dependency, or hand-roll the HTTP calls with
`Invoke-RestMethod`, `Invoke-WebRequest`, `curl` or `HttpClient` — the request log records the
`User-Agent`, and the verifier requires every `getAlerts` request to come from the SDK.

Useful starting points:

```powershell
Get-Command -Module VMware.Sdk.Vcf.Ops -Name '*Alert*', '*Token*', 'Connect-*', 'Disconnect-*'
Get-Help Invoke-VcfOpsGetAlerts -Full
```

## Contract

`docs/contract.json` was derived from the VCF Operations 9.0 OpenAPI document; every source
detail, including the pinned tag and commit, is recorded in `docs/official_sources.json`. It
names four operations and you may call no others:

| operationId | request |
| --- | --- |
| `acquireToken` | `POST /suite-api/api/auth/token/acquire` |
| `getCurrentVersionOfServer` | `GET /suite-api/api/versions/current` |
| `getAlerts` | `GET /suite-api/api/alerts` |
| `releaseToken` | `POST /suite-api/api/auth/token/release` |

Read the `pagination` and `client` sections of the contract before writing the pager: they
record the 0-based page origin, the termination rule, the array serialisation style, and the
rule that an unset optional parameter is absent from the query string rather than sent empty.

## Local mock

The verifier starts the mock itself, but you can drive it by hand:

```bash
python3 .protected/mock_vcf_ops.py --port 0 --log /tmp/requests.jsonl
```

It prints `PORT <n>`, builds its route table from `docs/contract.json`, answers 404 for
anything the contract does not name, and appends one JSON object per request to the log —
including the raw query string, repeated query keys, headers and body. That log is exactly
what the verifier reads.

## Verify

```bash
./run_tests.sh
```
