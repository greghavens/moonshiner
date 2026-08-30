# VcfAutomation.Change

Change-orchestration helpers for the **VCF Automation** APIs in VMware Cloud Foundation 9.1
(the successor to vRealize Automation / Aria Automation).

VCF PowerCLI 9.1 ships generated SDK bindings for SDDC Manager, Installer, Operations and
Cloud Builder — `VMware.Sdk.Vcf.SddcManager`, `VMware.Sdk.Vcf.Installer`,
`VMware.Sdk.Vcf.Ops`, `VMware.Sdk.Vcf.CloudBuilder` — and **none for VCF Automation**.
VCF Automation also has no specification in `vmware/vcf-api-specs`. So this module carries
the VCF Automation calls itself, against a contract transcribed by hand from the xAPIs
reference guides on developer.broadcom.com.

VCF PowerCLI is an **environment prerequisite** (`Install-Module -Name VCF.PowerCLI`). It is
never vendored into this repository. It is used on exactly one code path: bearer-token
acquisition, which goes through `VMware.Sdk.Vcf.SddcManager` because SDDC Manager's
`POST /v1/tokens` *is* covered by the SDK.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The five VCF Automation operations this module may call, hand-derived from reference docs. Read `provenance` first. |
| `docs/official_sources.json` | Every reference page consulted, the operation it documents, and the date fetched. |
| `src/VcfAutomation.Change/` | The module. `Private/` is the shipped HTTP and token layer; `Public/` is the surface. |
| `mock/Start-VcfaMock.ps1` | Loopback test double. Reads `docs/contract.json` and serves *only* the operations it names; logs every request as JSONL. |
| `tests/Verify-Change.ps1` | Verification. Drives the module against the double and asserts the exact wire shape. No VMware endpoint, no network beyond 127.0.0.1. |

## Running the checks

```pwsh
pwsh -NoLogo -NoProfile -File tests/Verify-Change.ps1
```

No Pester, no PowerCLI, no appliance required — the double stands in for the appliance and
`Connect-VcfaOrgSession -AccessToken` stands in for the token path.

## The rules that bite

* **Only contract operations.** `docs/contract.json` lists what may be called and which query
  parameters each operation documents. The double returns `501` for anything else and `400`
  for an undocumented query parameter, and logs both.
* **Unset optional fields are omitted, never sent empty.** See
  `clientRules.omitUnsetOptionalFields`. Every body field in both write operations is
  optional, so "did the caller supply this?" is the only signal the service gets.
* **Report, don't throw, and don't tidy up.** `Invoke-VcfaRequest` returns error statuses
  instead of raising them so a failing step can be reported. If a deployment was created
  before a later step failed, the operator has to be told its id — deleting it is out of
  contract.
