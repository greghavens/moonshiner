# VCF Operations for Logs forwarder rollout

This starter repository contains a contract slice from the VCF Operations for
Logs 9.0 OpenAPI specification, a loopback-only contract mock, and a rollout
scenario. The implementation work is limited to the PowerShell module in
`src/` and the runner in `scripts/`.

The `VMware.Sdk.Vcf.Ops` VCF PowerCLI module is an environment prerequisite.
It is intentionally not included in this repository.

Run the deterministic check with:

```sh
python3 tests/verify.py
```
