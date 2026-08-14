# VCF Automation policy validation adapter

This starter contains a small PowerShell adapter for the VCF Automation 9.0
policy API. The generated `VMware.Sdk.Vcf` PowerCLI modules remain an external
prerequisite; this project fills the Automation API gap with a checked-in,
reference-derived REST contract.

The implementation target is
`VcfAutomation.Policy/VcfAutomation.Policy.psm1`. The protected verifier starts
the contract-pinned server only on `127.0.0.1`, exercises successful and failed
validation, and reads its JSONL request log.

Run the verification with:

```powershell
pwsh -NoLogo -NoProfile -File tests/verify.ps1
```
