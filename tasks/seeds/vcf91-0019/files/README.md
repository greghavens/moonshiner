# VCF 9.1 managed-credential rotation

Implement the concurrency-safe REST workflow in
`vcf_credential_rotation/client.py`. The public imports are declared in
`vcf_credential_rotation/__init__.py`.

The task-scoped contract in `docs/contract.json` was projected from the
commit-pinned VMware Cloud Foundation 9.1 SDDC Manager OpenAPI specification.
The implementation must use only the Python standard library.

Run the protected acceptance verifier with:

```console
python3 -B .moonshiner/verify.py
```

Verification uses a local HTTP server bound to an ephemeral `127.0.0.1` port.
It does not contact a VMware installation or any other network service.
