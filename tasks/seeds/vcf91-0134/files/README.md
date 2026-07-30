# VCF 9.1 Supervisor/VKS provisioning exercise

Implement `New-VcfVksCluster` in `src/VcfVksProvisioning.psm1`.

The checked-in contract is a small, reviewable extraction from the official VCF
9.1 vSphere Automation OpenAPI specification. The local mock reads the same
contract and rejects every route that the contract does not name. Acceptance
tests start the mock on an ephemeral loopback port and inspect its JSON-lines
request log; they do not contact SDDC Manager, vCenter, a Supervisor, or any
other live VMware endpoint.

VCF PowerCLI is an environment prerequisite. In particular,
`VMware.Sdk.Vcf.SddcManager` must be installed. It is intentionally not included
in this repository.

Run the acceptance suite with:

```text
python3 tests/verify.py
```

The contract and verifier are protected task inputs and must not be edited.
