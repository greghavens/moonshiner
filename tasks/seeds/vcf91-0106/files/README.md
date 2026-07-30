# vcf-diag

This exercise implements a small, dependency-free vCenter client for collecting
evidence after a host TPM attestation failure.

The checked-in contract is a focused extraction from the official VCF 9.1
OpenAPI specification. It is deliberately small enough to audit by hand. The
acceptance fixture binds only to loopback and serves only those contract
operations.

Run the protected acceptance verifier with:

```sh
python3 grader_tests/verify.py
```
