# VCF Installer precheck client

Implement the `vcf_installer` package described by the task. The checked-in
contract is a focused extraction of the pinned VCF Installer 9.1 OpenAPI
specification.

Run the acceptance check with:

```sh
python3 tests/verify.py
```

The test uses only a loopback HTTP server and the Python standard library.
