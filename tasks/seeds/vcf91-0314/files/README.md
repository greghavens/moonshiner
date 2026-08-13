# VcfAutomation.Triage

Triage tooling for VCF Automation 9.1 deployments, for the payments platform
on-call runbook.

## Layout

| Path | What it is |
| --- | --- |
| `src/VcfAutomation.Triage/` | The PowerShell module. `VcfAutomation.Triage.psm1` is where the work goes. |
| `docs/contract.json` | The REST contract for the VCF Automation operations this module may call. |
| `docs/official_sources.json` | Every reference page the contract was transcribed from, the operation each documents, and the date it was read. |
| `mock/vcfa_mock.py` | A loopback stand-in for a VCF Automation appliance, pinned to `docs/contract.json`. |
| `mock/fixtures.py` | The state the mock serves. |
| `tests/verify.py` | The verification suite. |
| `tests/run_triage.ps1` | Driver the suite uses to invoke the module. |

## About the contract

VCF Automation has no published API specification. It is absent from the
`vmware/vcf-api-specs` repository, and Broadcom ships no OpenAPI document for
these operations. `docs/contract.json` was therefore transcribed by hand from
the authoritative xAPIs reference pages on `developer.broadcom.com`, and it says
so in its own `sourceStatement`. It covers only the operations this module
needs. If you need a detail it does not record, the page that documents each
operation is linked from the operation's `docUrl` and from
`docs/official_sources.json`.

Because there is no specification, there is also no generated PowerShell
binding. VCF PowerCLI's `VMware.Sdk.Vcf.*` family covers CloudBuilder, SDDC
Manager, Installer and Operations only. VCF PowerCLI is installed on the
operator workstation as a prerequisite and is declared in the module manifest
under `ExternalModuleDependencies`; it is never vendored into this repository.
The VCF Automation calls in this module are hand-rolled REST against the
contract.

## Running the mock by hand

```sh
python3 mock/vcfa_mock.py --port 8080 --request-log /tmp/vcfa-requests.jsonl
```

It binds to `127.0.0.1` only and accepts the bearer token `mock-access-token`
unless `--token` says otherwise. It serves *only* the operations named in the
contract and rejects anything that violates the contract's `wireRules`, so it is
a useful check on your request shapes while you work. Every request it receives
is appended to the request log as one JSON object per line.

## Verifying

```sh
python3 tests/verify.py
```

The suite starts its own mock on a free loopback port. It contacts no VMware
endpoint and requires no network access.
