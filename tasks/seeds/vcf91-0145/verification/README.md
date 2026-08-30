# vks-diag

This exercise implements a dependency-free evidence collector spanning a
vSphere Supervisor namespace and a VKS cluster's Kubernetes API.

`docs/contract.json` is a focused extraction from the official VCF 9.1
vSphere Automation OpenAPI specification. The protected vCenter fixture binds
only to loopback and serves only the operations named by that contract. A
separate loopback Kubernetes fixture supplies pod, Event, and log evidence.

Run the protected acceptance verifier with:

```sh
python3 grader_tests/verify.py
```
