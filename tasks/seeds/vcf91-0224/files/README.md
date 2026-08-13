# VcfSddcLcm — retry-safe support bundle generation (VCF 9.1)

Operations calls SDDC LCM to collect component support bundles during upgrade
triage. The collection endpoint is **not idempotent**: every `POST` mints a new
task and a new multi-gigabyte bundle. Runbooks get re-run, CI jobs get retried,
and operators double-click — so we keep generating duplicate bundles and filling
the appliance datastore.

Your job is to implement `src/VcfSddcLcm` so that submitting the same logical
request twice produces **one** bundle.

## What to implement

`src/VcfSddcLcm/VcfSddcLcm.psm1` currently has stubs that throw. Implement them.
Do not change the exported function names or parameters — `tests/verify.ps1`
calls them exactly as declared.

### `New-VcfSddcLcmSession`

Returns a session carrying the appliance base URI and the bearer token. It
accepts either an explicit `-Server`/`-Token` pair, or a `-Connection` object
from `Connect-VcfSddcManagerServer`. SDDC LCM is served from the same appliance
as SDDC Manager, under the `/sddc-lcm` base path, using the same bearer token.
The SDK connection exposes those values as `ServiceUri` and `SessionSecret`.

### `Start-VcfSddcLcmSupportBundle`

```powershell
Start-VcfSddcLcmSupportBundle -Session $s -ComponentId <uuid> -CorrelationId <string>
                              [-LookBackWindow <int>] [-Wait]
                              [-TimeoutSeconds <int>] [-PollIntervalSeconds <int>]
```

Required behaviour:

1. **Check before submitting.** Call `getTasks` filtered to the component and
   look through every result page for a task already tagged with
   `-CorrelationId`. Send only the filters you actually mean — the operation
   declares fifteen optional query parameters and the ones you are not using
   must not appear on the wire at all. The initial request omits `pageNumber`;
   add it only when another page is required.
2. **Adopt, don't duplicate.** If such a task exists, return it and do **not**
   `POST`. Report this via `Reused = $true`.
3. **Otherwise submit** `generateComponentSupportBundle`, tagging the request
   with the `X-Correlation-Id` header so the next run can find it.
4. **With `-Wait`**, poll `getTask` until the task reaches a terminal state, then
   resolve the produced bundle through `getComponentSupportBundles`.

Return a single object with `Task`, `Reused`, and `SupportBundle` (populated only
when `-Wait` ran to a successful completion).

### The request body

`ComponentSupportBundleSpec` has exactly one property, `lookBackWindow`, and it
is optional. When the caller does not pass `-LookBackWindow`, the property must
be **absent from the JSON body**, which must serialise as `{}`.

Sending `{"lookBackWindow":0}` or `{"lookBackWindow":null}` is wrong and not
equivalent: `0` pins the window to zero hours, whereas omitting the key lets the
service apply its own default. The same rule applies to query parameters —
omit them rather than sending them empty.

## Ground rules

- `docs/contract.json` is the authoritative wire contract, derived from
  `specifications/sddc-lcm/sddc-lcm-openapi.yaml` in
  [vmware/vcf-api-specs](https://github.com/vmware/vcf-api-specs) at the commit
  recorded in `docs/official_sources.json`. Read it. Do not edit it.
- Only the four operations named in the contract may be called.
- `VMware.Sdk.Vcf.SddcManager` is installed by the environment and declared in
  the module manifest. **Never vendor it into this repository.** Note that it
  ships no cmdlet for the support-bundle operations, which is why this module
  speaks the documented wire contract directly.
- `docs/`, `mock/` and `tests/` are protected. Solve the task in `src/`.

## Running the checks

```powershell
pwsh -File tests/verify.ps1
```

This uses a deterministic in-process transport double pinned to the contract,
exercises your module, and asserts the exact shape of every request without
opening a socket. No VMware endpoint is contacted.
