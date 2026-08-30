# VcfOpsActionRunner

A small PowerShell module that runs **VMware Cloud Foundation Operations**
actions (VCF 9.0) and waits for them to finish.

Submitting an action to VCF Operations is asynchronous: the appliance answers
with the identifiers of the tasks it started, and the caller has to poll those
tasks until they settle. This module wraps that dance so callers get one call
and one result object.

## Layout

| Path | What it is |
| --- | --- |
| `src/VcfOpsActionRunner/` | The module. This is the only place you need to write code. |
| `docs/contract.json` | The wire contract, derived from the VCF Operations OpenAPI document. |
| `docs/official_sources.json` | Where every fact in the contract came from, down to a JSON Pointer. |
| `tests/mock/` | A loopback stand-in for a VCF Operations appliance, pinned to `docs/contract.json`. |
| `tests/Verify-Contract.ps1` | Contract verification. Reads the mock's request log and asserts the exact bytes that went out. |

## Prerequisite

`VMware.Sdk.Vcf.Ops` (the generated VCF Operations PowerCLI client) is installed
by the environment. This repository deliberately does not vendor it, and the
module must not reach the appliance by any other route — no `Invoke-RestMethod`,
no `HttpClient`, no hand-rolled URLs.

```powershell
Get-Module -ListAvailable VMware.Sdk.Vcf.Ops
```

## Running the checks

```powershell
pwsh -NoProfile -File tests/Verify-Contract.ps1
```

The verifier starts the mock on an ephemeral loopback port, connects the client
to it, exercises the module, and then inspects `tests/.artifacts/requests.jsonl`.
It never contacts a real VMware endpoint. Add `-Detailed` to see every passing
assertion.

## Contract notes

`docs/contract.json` is the source of truth for both the mock's route table and
the verifier's expectations. Two things it is strict about:

* **Unset optional fields are omitted.** A property that is not in a schema's
  `required` list and that the caller did not supply must be absent from the
  request body. `null`, `""` and `[]` are all wrong: the appliance cannot tell
  them apart from a deliberate empty value. The same goes for optional query
  parameters.
* **A submission is not a completion.** `performAction` returning `200` only
  means tasks were started. The action's outcome comes from polling
  `getActionStatus` until it reports a terminal state.
