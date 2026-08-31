# VCF 9.1 Supervisor/VKS provisioning exercise

Implement `New-VcfVksCluster` in `src/VcfVksProvisioning.psm1`.

VCF PowerCLI is an environment prerequisite. In particular,
`VMware.Sdk.Vcf.SddcManager` must be installed. It is intentionally not included
in this repository.

Run the acceptance suite with:

```text
python3 tests/verify.py
```
