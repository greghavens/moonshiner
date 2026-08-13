# vcf-ops-credential-rotation

Operational tooling for rotating the service-account secret that VMware Cloud
Foundation Operations (VCF 9.1) uses to collect from vCenter.

## Why this exists

The obvious way to rotate a credential is to overwrite its `fields` in place
with `partialUpdateCredential`. Every adapter instance bound to that credential
picks up the new secret at once — including the ones already mid-collection.
Those cycles fail against a secret that no longer exists, and the collectors
back off before anyone notices.

So the rotation stages a replacement credential, moves each adapter instance
onto it, proves nothing still points at the original, and only then marks the
original retired.

## Layout

| Path | Purpose |
| --- | --- |
| `src/VcfOpsCredentialRotation/` | the module (implement `Invoke-VcfOpsCredentialRotation` here) |
| `docs/contract.json` | the operations this module may use, derived from the OpenAPI spec |
| `docs/official_sources.json` | provenance: spec path, pinned commit, operationIds |
| `tools/Start-VcfOpsMock.ps1` | loopback stand-in for the appliance, pinned to the contract |
| `tools/mock-state.json` | the fixture the mock starts from |
| `tools/derive_contract.py` | regenerates `docs/contract.json` from the spec |
| `tests/Verify-Rotation.ps1` | the acceptance check |

## The API contract

`docs/contract.json` is generated from
`specifications/vcf-operations/vcf-operations-openapi.json` in
[vmware/vcf-api-specs](https://github.com/vmware/vcf-api-specs) at commit
`c3f3b52c845dd967cabbc21680e893292077d5ba` (VCF Operations API 9.1.0.0, base
path `/suite-api`). It names the eight operations this module is allowed to
call:

| operationId | | |
| --- | --- | --- |
| `acquireToken` | `POST` | `/api/auth/token/acquire` |
| `getCurrentVersionOfServer` | `GET` | `/api/versions/current` |
| `getCredentials` | `GET` | `/api/credentials` |
| `createCredential` | `POST` | `/api/credentials` |
| `enumerateAdapterInstances` | `GET` | `/api/adapters` |
| `patchAdapterInstance` | `PATCH` | `/api/adapters` |
| `partialUpdateCredential` | `PATCH` | `/api/credentials` |
| `releaseToken` | `POST` | `/api/auth/token/release` |

Anything outside that list is refused by the mock and fails the acceptance
check. `acquireToken` and `releaseToken` are reached through
`Connect-VcfOpsServer` / `Disconnect-VcfOpsServer`.

All traffic must go through the `VMware.Sdk.Vcf.Ops` cmdlets, which the
environment installs. Do not hand-roll HTTP and do not vendor the SDK.

> Note: the SDK's uuid **path** parameters are broken in 13.5.0
> (`ClientUtils.FormatPathParameter` reflects over `System.Guid` instead of
> stringifying it), which is why the contract sticks to the collection
> endpoints that take the id in the query string or the body.

## Required behaviour

`Invoke-VcfOpsCredentialRotation` takes `-Server -Port -Protocol -User
-Password -AdapterKind -CredentialName -NewCredentialName -NewSecret
-SecretFieldName` and an optional `-MaxAttempts` (default 3), and must:

1. Connect, then find the single credential of `-AdapterKind` named
   `-CredentialName`. Fail loudly if it is not unique.
2. Create the replacement with `-NewCredentialName`, carrying the original's
   `adapterKindKey` and `credentialKindKey`, and one `fields` entry
   `{ name = -SecretFieldName, value = -NewSecret }`.
3. Repoint every adapter instance currently bound to the original onto the
   replacement — and nothing else. Adapters belonging to other credentials
   must be left alone.
4. Re-read the adapter instances afterwards to confirm none still reference the
   original.
5. Only if that check comes back clean, rename the original to
   `"<CredentialName> (retired)"` via `partialUpdateCredential`. Its `fields`
   must not be rewritten.
6. Release the session.

Returns:

```
OldCredentialId       [string]
NewCredentialId       [string]
RepointedAdapterIds   [string[]]  ascending
Drained               [bool]
Retired               [bool]
RetiredCredentialName [string]
```

### Request shape

The appliance distinguishes "not supplied" from "supplied as empty". Optional
properties you are not setting must be **absent** from the JSON, not sent as
`null` or `""`. In particular:

* `createCredential` must not carry an `id` — the spec requires it to be null
  on creation — and must not carry `editable`.
* `patchAdapterInstance` carries only `id`, `resourceKey` and
  `credentialInstanceId`. `resourceKey` carries only `adapterKindKey`, `name`
  and `resourceKindKey`. Do not echo back the nulls the enumeration returned.
* `partialUpdateCredential` must carry `id` (the spec requires it for every
  non-creation request).

The `Initialize-VcfOps*` model cmdlets already omit properties you never
assign; building bodies as raw hashtables tends not to.

### Collectors that are busy

A collector part-way through a cycle can reject a repoint with `503`. That is
retryable and must be retried, up to `-MaxAttempts`. An adapter left on the
original credential means step 4 fails and the original must not be retired.

## Running the check

```
pwsh -NoProfile -File tests/Verify-Rotation.ps1
```

It starts the mock on a free loopback port, runs one rotation, and asserts the
recorded requests. It then repeats against fresh state where one collector
remains busy through the configured attempt limit, proving that the original
credential is not retired and the session is still released. It never contacts
a live VMware endpoint. Exit code 0 passes.
