# Vcf.Nsx.Policy.Async

This repository contains a small VCF 9.1 PowerShell integration module. Its job is to submit an NSX Policy infra segment and wait for policy realization instead of treating the successful `PATCH` response as completion.

The official VCF PowerCLI distribution is an environment prerequisite. Do not copy its modules, generated bindings, or assemblies into this repository. The relevant generated SDK module is `VMware.Sdk.Nsx.Policy` 13.5 or later.

## Public commands

Implement these exported functions in `src/Vcf.Nsx.Policy.Async/Vcf.Nsx.Policy.Async.psm1`:

- `New-VcfNsxPowerCliTransport` returns the production transport. It must use `Initialize-SegmentSubnet`, `Initialize-Segment`, `Invoke-PatchInfraSegment`, and `Invoke-ReadIntentStatus` from VCF PowerCLI. Construct initializer splats by presence so absent optional values stay absent.
- `Set-VcfNsxInfraSegment` submits the segment intent and polls it to a terminal state.

`Set-VcfNsxInfraSegment` keeps the parameter surface in the starter file. The required segment fields are `SegmentId`, `DisplayName`, `GatewayAddress`, and `ConnectivityPath`. Supported optional segment fields are `Description`, `TransportZonePath`, `AdminState`, `OverlayId`, and `VlanIds`. If `Transport` is omitted, create the production transport with `New-VcfNsxPowerCliTransport`.

For a successful call, return one object with these properties:

- `SegmentId`
- `IntentPath`
- `Status` (`SUCCESS`)
- `PollCount`
- `Response` (the final `ReadIntentStatus` response)

Throw on the terminal `ERROR` state. Poll `IN_PROGRESS`, `UNKNOWN`, `UNINITIALIZED`, and `SANDBOXED_REALIZATION_PENDING` until the monotonic timeout expires. Reject any response with a missing or unsupported consolidated status rather than silently reporting success.

## Transport seam

The injectable `Transport` is a script block invoked once per operation with a request object. It returns the decoded operation response. A request has the spec operationId plus its method, relative path, path parameters, query values, and body:

```text
OperationId, Method, Path, PathParameters, Query, Body
```

Use operationId `PatchInfraSegment` for the first request. Its relative path is `/infra/segments/{escaped-segment-id}` and its body contains the supplied contract fields. Use operationId `ReadIntentStatus` for every poll. Its relative path is `/infra/realized-state/status`, with only required `intent_path` in the query unless an optional query value was explicitly supplied by a future caller. The intent path for this workflow is `/infra/segments/{segment-id}`.

The acceptance verifier supplies an HTTP transport to a loopback-only mock. It checks the raw request log, including URI escaping, JSON bytes, headers, poll count, terminal handling, and omission of every unset optional field. It never connects to a VMware endpoint.

Run:

```sh
python3 tests/verify.py
```

## Contract provenance

`docs/contract.json` is a reduced, machine-readable extraction of the two named operations from the VCF 9.1 NSX Policy OpenAPI 2.0 specification. `docs/official_sources.json` pins the repository commit, exact source path, license, and operationIds. The upstream specification is not vendored.
