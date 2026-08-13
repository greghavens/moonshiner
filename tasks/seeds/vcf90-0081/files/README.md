# VCF Operations for Logs upgrade integration

This fixture contains an intentionally unfinished PowerShell module. Implement
`Invoke-VcfLogsUpgrade` in `src/Vcf.OperationsForLogs.psm1` against the
contract-pinned loopback service.

The VCF 9.0 PowerCLI prerequisite is `VMware.Sdk.Vcf.Ops` version
`13.4.0.24798382`. It is supplied by the execution environment and is declared
in the module manifest; it is not part of this repository.

Run the acceptance check with:

```sh
python3 tests/verify.py
```

The check starts `mock/vcf_logs_mock.py` on an ephemeral loopback port. It does
not contact a VMware appliance or any public service.
