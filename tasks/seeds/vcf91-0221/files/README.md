# VCF SDDC lifecycle PowerCLI companion

This repository contains the starter for a small PowerShell companion module for
VMware Cloud Foundation 9.1. The module configures SDDC LCM with SDDC Manager and
Fleet LCM, then waits for the asynchronous task to reach a terminal state.

The REST surface is intentionally narrow. `docs/contract.json` is a derived
contract for exactly two operations from VMware's Apache-2.0 API specification,
and `docs/official_sources.json` records the immutable source provenance. The
loopback service in `tests/mock_sddc_lcm.py` serves only those operations.

## Prerequisites

- PowerShell 7.4 or later
- `VMware.Sdk.Vcf.SddcManager` from VCF PowerCLI 9.1
- Python 3 (verification only)

The VMware module is supplied by the environment. Do not copy, generate, stub,
or vendor any `VMware.Sdk.Vcf*` package or assembly into this repository.

## Work to complete

Implement `Set-VcfSddcLcmConfiguration` in
`VcfSddcLcm/VcfSddcLcm.psm1`. Keep the exported command and its parameters as
declared in the starter. The implementation must:

1. Build a `ConfigSpec` request using lower-camel-case property names from the
   derived contract.
2. Send `setConfig` to the caller-provided base URI with JSON and bearer
   authentication.
3. Omit every unset optional property. In particular, do not serialize nulls,
   empty strings, or placeholder objects for `vspCluster`, `vcenter`, Fleet LCM
   `username`, `password`, or `opsToken`.
4. Read the task `id` from the accepted response and call `getTask` repeatedly.
   `PENDING`, `SCHEDULED`, and `RUNNING` are nonterminal. Return the terminal
   Task object only when its status is `SUCCEEDED`; throw for `FAILED`,
   `CANCELED`, an unknown state, a malformed response, or timeout.
5. Honor `PollIntervalSeconds` (including zero for local verification) and
   `TimeoutSeconds`. Always perform at least one `getTask` request; a `202`
   response is not completion.

`BaseUri` is mandatory so the verifier can force all traffic to a loopback
endpoint. Do not hard-code or fall back to the server URL shown in the upstream
specification.

Run the acceptance check with:

```sh
python3 tests/verify.py
```
