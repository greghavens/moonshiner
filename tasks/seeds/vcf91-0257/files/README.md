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

## Required behaviour

`Invoke-VcfOpsCredentialRotation` takes `-Server -Port -Protocol -User
-Password -AdapterKind -CredentialName -NewCredentialName -CredentialField
-NewSecret -SecretFieldName` and optional `-SkipCertificateCheck` and
`-MaxAttempts` (default 3), and must:

1. Connect, then find the single credential of `-AdapterKind` named
   `-CredentialName`. Fail loudly if it is not unique.
2. Create the replacement with `-NewCredentialName`, carrying the original's
   `adapterKindKey` and `credentialKindKey`. `fields` contains each ordered
   `-CredentialField` entry followed by
   `{ name = -SecretFieldName, value = -NewSecret }`; the non-secret fields are
   required because credential kinds such as `PRINCIPALCREDENTIAL` reject a
   password-only replacement.
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
* `patchAdapterInstance` carries `id`, `resourceKey`, `credentialInstanceId`,
  and `adapter-certificates: []`. The deployed service rejects the PATCH when
  `adapter-certificates` is omitted because the generated model serializes it
  as null. `resourceKey` carries only `adapterKindKey`, `name` and
  `resourceKindKey`. Do not echo back the other nulls the enumeration returned.
* `partialUpdateCredential` must carry `id` (the spec requires it for every
  non-creation request).

The `Initialize-VcfOps*` model cmdlets already omit properties you never
assign; building bodies as raw hashtables tends not to.

### Collectors that are busy

A collector part-way through a cycle can reject a repoint with `503`. Only that
status is retryable and must be retried, up to `-MaxAttempts`; other failures
terminate immediately. An adapter left on the
original credential means step 4 fails and the original must not be retired.

## Running the check

```
pwsh -NoProfile -File tests/Verify-Rotation.ps1
```

The checks cover both a successful rotation and a collector that remains busy
through the configured attempt limit. Exit code 0 passes.
