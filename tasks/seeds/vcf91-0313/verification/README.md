# vcfa-credential-rotation

Rotates the credentials of a VCF Automation cloud account on VMware Cloud
Foundation 9.1, without stranding requests that are already in flight against
the old secret.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The API contract this client is written against. **Authority for every field.** |
| `docs/official_sources.json` | Where each part of the contract came from, and when it was fetched. |
| `src/VcfAutomation.CredentialRotation/` | The PowerShell module. `.psm1` is where the work goes. |
| `tools/mock/vcfa_mock_server.py` | Loopback mock of the IaaS API, pinned to the contract. |
| `tests/Invoke-Verification.ps1` | The verification suite. |

## The contract is reference-derived

VCF Automation has no OpenAPI specification in `vmware/vcf-api-specs`. There is
nothing to generate a client from. `docs/contract.json` was transcribed by hand
from the xAPIs reference pages for the **VM Apps Org - Provisioning Service** on
`developer.broadcom.com`; `docs/official_sources.json` records every page URL,
the operation it documents, and the date it was read.

Treat `docs/contract.json` as authoritative and do not widen it. It names five
operations and that is the entire surface:

| | |
| --- | --- |
| `GET` | `/iaas/api/cloud-accounts/{id}` |
| `GET` | `/iaas/api/request-tracker` |
| `GET` | `/iaas/api/request-tracker/{id}` |
| `PATCH` | `/iaas/api/cloud-accounts/{id}` |
| `POST` | `/iaas/api/cloud-accounts/{id}/health-check` |

Every request carries `apiVersion=2021-07-15` in the query string and
`Authorization: Bearer <token>`.

## VCF PowerCLI is a prerequisite

`VMware.Sdk.Vcf.SddcManager` is installed in the environment and is declared in
`RequiredModules` in the module manifest. Do not vendor a copy of it into this
repository, and do not remove the dependency.

Its model builders (the `Initialize-Vcf*` cmdlets) establish the serialisation
convention this module follows: a model property the caller never set does not
appear in `ToJson()` output at all.

```powershell
PS> (Initialize-VcfTokenCreationSpec -Username u -Password p).ToJson()
{
  "username": "u",
  "password": "p"
}
```

`TokenCreationSpec` also has `apiKey` and `idToken`. They are unset, so they are
absent — not `null`, not `""`.

## What to build

Two exported functions, both currently throwing `NotImplementedException`.

### `New-VcfaUpdateCloudAccountSpecification`

Builds the `PATCH` body and returns an object with a `ToJson()` method.

`-Name`, `-CloudAccountProperties` and `-Regions` are mandatory. Everything else
is optional and **must be omitted from the JSON entirely when the caller does not
supply it**. The distinction that matters: omission keys off whether the caller
*bound the parameter*, not off whether the value is falsy. `-CreateDefaultZones
$false` and `-Description ''` are things the caller asked for and must go on the
wire; leaving them unbound must produce no key.

This is not pedantry. `PATCH` here is a partial update, so a present-but-empty
value is a request to *clear* the field. Sending `"tags": []` while rotating a
password silently strips the operator's tags off the cloud account.

Each `-Regions` entry must serialise to exactly `externalRegionId` and `name`.

### `Invoke-VcfaCloudAccountCredentialRotation`

```powershell
Invoke-VcfaCloudAccountCredentialRotation `
    -Server https://vcfa.corp.example.net `
    -AccessToken $token `
    -CloudAccountId ca-... `
    -NewPrivateKeyId 'svc-account@vsphere.local' `
    -NewPrivateKey $secret
```

Returns a `[pscustomobject]`:

| Property | |
| --- | --- |
| `DrainedRequestIds` | trackers that were in flight and were waited on |
| `RotationRequestId` / `RotationStatus` | the update's tracker and its terminal status |
| `HealthCheckRequestId` / `HealthCheckStatus` | the health check's tracker and its terminal status |
| `Succeeded` | both statuses are `FINISHED` |

Required behaviour:

1. **Drain first.** Before rotating, list request trackers and follow anything
   `INPROGRESS` until it reaches `FINISHED` or `FAILED`. A request that is
   already running was admitted under the old secret and will keep
   authenticating with it; rotating underneath it strands it mid-flight with no
   clean rollback. Only once nothing is `INPROGRESS` may the `PATCH` go out.
2. **Read before you write.** `name`, `cloudAccountProperties` and `regions` are
   all *required* on the update. Carry `name` and `cloudAccountProperties`
   forward verbatim from `GET /iaas/api/cloud-accounts/{id}`, and build `regions`
   by projecting the account's `enabledRegions` down to `{externalRegionId,
   name}`. `enabledRegions` returns `Region`; the request takes
   `RegionSpecification`. They are not the same type.
3. **`202` is not success.** The update and the health check both return `202`
   with a tracker in `INPROGRESS`. Poll `GET /iaas/api/request-tracker/{id}`
   until `FINISHED` or `FAILED`.
4. **Verify afterwards.** Once the update has finished, run the endpoint health
   check and follow its tracker to a terminal status too.
5. **Keep the secret out of URLs and headers.** It belongs in the `privateKey`
   field of the update body and nowhere else.

## Running the checks

```
pwsh -File tests/Invoke-Verification.ps1
```

The suite starts the mock on `127.0.0.1` on an ephemeral port, drives the module
against it, and then asserts the exact wire shape of every request from the
mock's JSON Lines log. No live VMware endpoint is contacted.

The mock's state machine advances on observation counts rather than elapsed
time, so runs are reproducible at any polling speed. The pre-existing in-flight
request settles after it has been observed three times — you have to actually
poll it, not sleep past it.
