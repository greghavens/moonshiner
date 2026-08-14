# VCF Operations for Networks change module

This repository contains a small PowerShell integration for a two-step vCenter
data-source change in VMware Cloud Foundation 9.0.

The `VMware.Sdk.Vcf.Ops` PowerCLI module is an installed prerequisite. It is not
part of this repository. The Operations for Networks REST routes used here are
captured in `docs/contract.json`, with their pinned upstream provenance in
`docs/official_sources.json`.

## Compact document shapes

`docs/contract.json` has the top-level fields `openapi`, `apiVersion`,
`basePath`, and `operations`. Each member of `operations` has `operationId`,
uppercase `method`, `path`, `pathParameters`, `requestBody`, and `responses`.
Normalize each path parameter to `name`, `in`, `required`, and `type`. Represent
response status codes as integers. A bodyless operation uses `null` for
`requestBody`; otherwise that object has `required`, `mediaType`, `schema`,
`mutableProperties`, and `requiredProperties`.

`docs/official_sources.json` has `repository`, `license`, `tag`, `commit`,
`specPath`, and `operations`. Each provenance operation has `operationId`,
uppercase `method`, and `path`, in specification order.

Run the repository verifier from the repository root:

```text
python3 .moonshiner/verify.py
```
