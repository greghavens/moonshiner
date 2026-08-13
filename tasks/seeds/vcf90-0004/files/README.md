# VCF 9.0 network pool provisioning

`VcfNetworkPoolProvisioning.psm1` provisions an SDDC Manager network pool from a
VMware Cloud Foundation 9.0 estate, driven by the genuine
`VMware.Sdk.Vcf.SddcManager` PowerCLI module.

## Layout

| Path | Purpose |
| --- | --- |
| `VcfNetworkPoolProvisioning.psm1` | The module. This is the only file to change. |
| `docs/contract.json` | The REST contract this module is written against. |
| `docs/official_sources.json` | Where that contract came from. |
| `.protected/` | Acceptance harness. Do not change. |

## The contract

`docs/contract.json` is a focused projection of
`specifications/sddc-manager/sddc-manager-openapi.json` from VMware's Apache-2.0
[`vmware/vcf-api-specs`](https://github.com/vmware/vcf-api-specs) repository, taken
from the specification itself at tag `9.0.0.0` rather than from a documentation
page. `docs/official_sources.json` records the tag, the commit it resolves to and
each operationId. Read the contract for the wire shape; the 9.1.0.0 revision of
that same file is a different contract and does not apply here.

Three operations are in scope: `createToken`, `getNetworkPool` and
`createNetworkPool`.

## Running the checks

```
pwsh -NoLogo -NoProfile -File .protected/verify.ps1
```

The harness connects the real SDK to a loopback SDDC Manager that only answers
the operations the contract names, then reads its request log. It never contacts
a live VMware endpoint.
