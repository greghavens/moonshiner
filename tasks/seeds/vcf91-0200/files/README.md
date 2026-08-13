# VCF Installer depot token retry

Implement `Set-VcfInstallerDepotToken` in
`src/VcfInstaller.Depot/VcfInstaller.Depot.psm1`.

The supplied manifest declares the VMware VCF PowerCLI prerequisite. The
prerequisite is installed by the runner and is intentionally not included in
this repository. `docs/contract.json` is a focused wire-contract projection of
the VCF Installer 9.1 OpenAPI specification pinned by
`docs/official_sources.json`.

Run the protected verifier with:

```console
python3 -B tests/verify.py
```

The verifier starts a loopback-only contract mock. It does not contact a live
VCF Installer.
