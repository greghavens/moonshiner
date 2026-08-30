# VCF Operations outbound notification onboarding

Complete `New-VcfOpsOutboundNotification` in
`src/VcfOps.OutboundNotification/VcfOps.OutboundNotification.psm1`.

The command is a small integration layer over the VCF Operations 9.0 PowerCLI
SDK (`VMware.Sdk.Vcf.Ops`). It accepts an already connected VCF Operations
`Server` object and applies one change in three ordered steps:

1. `createAlertPlugin` — create the notification plugin instance. Create it
   *without* an `enabled` flag; the instance is activated by step 2.
2. `updateAlertPlugin` — resend the instance with the `pluginId` returned by
   step 1 and `enabled` set to true.
3. `createNotificationPluginRule` — bind a notification rule to that instance.

Step 3 fails when the server has no matching notification template. The point of
the exercise is what happens then: the first two steps have already changed the
appliance, and the result must say so. Do not roll the change back, and do not
report the whole operation as if nothing had been applied.

## Result shape

Return a single object with these properties:

| Property          | Meaning                                                       |
| ----------------- | ------------------------------------------------------------- |
| `Succeeded`       | `$true` only when all three steps succeeded                    |
| `PluginId`        | plugin id the server assigned, `''` if step 1 never succeeded  |
| `RuleId`          | notification rule id, `''` if no rule was created              |
| `RequiresCleanup` | `$true` when the run failed after at least one step applied    |
| `Steps`           | exactly three entries, in order                                |

Each entry in `Steps` carries `Index` (1..3), `OperationId`, `Status`
(`Succeeded`, `Failed` or `Skipped`), `StatusCode` (the HTTP status for a failed
step, otherwise `$null`) and `Message`. For the failing step, `Message` must
carry the reason the server returned, not a generic string.

SDK cmdlets report transport failures as non-terminating errors, so call them
with `-ErrorAction Stop` if you intend to catch them.

## Wire shape

Build request bodies with the SDK's `Initialize-VcfOps*` cmdlets and pass only
the parameters the caller actually bound. An optional field the caller did not
supply must be absent from the JSON body — not `""`, not `[]`, not `null`.
`ConfigValues` is a hashtable; emit it as `name`/`value` pairs ordered by name
using an ordinal, case-sensitive sort so the body is deterministic.

Pass the supplied `Server` object to every SDK call. Do not use
`Invoke-RestMethod`, `Invoke-WebRequest`, `WebClient`, `HttpClient`, `curl` or
any other direct HTTP client.

## Sources and verification

The spec-derived operation subset is `docs/contract.json`; its pinned provenance
is `docs/official_sources.json`. Both are generated from tag `9.0.0.0` of the
`vmware/vcf-api-specs` repository by `tools/derive_contract.py`, which takes a
local copy of the upstream OpenAPI document as its only argument. The full
VMware specification and the PowerCLI dependency are deliberately not vendored.

Run the protected verifier with:

```sh
python3 tests/verify.py
```

No live VMware endpoint is contacted. The verifier starts the contract-pinned
loopback service in `tests/support/vcf_ops_mock.py`, which serves only the
operations the contract names, and then checks its request log.
