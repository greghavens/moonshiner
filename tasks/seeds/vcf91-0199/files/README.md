# VCF Installer task inventory

Complete `Get-VcfInstallerTaskInventory` in
`src/VcfInstaller.TaskInventory/VcfInstaller.TaskInventory.psm1`.

The command is a small integration layer over the VCF PowerCLI 9.1 SDK. It must
use `Invoke-VcfInstallerGetTasks`, collect all one-based pages, and emit the
result in a deterministic order: `CreationTimestamp` ascending, then `Id`
ascending.

The command accepts an already connected VCF Installer `Server` object. The
verifier supplies that connection and a `PageSize` of 3. Optional filters are
forwarded only when present in `PSBoundParameters`; do not turn an absent value
into an empty query value or into the OpenAPI default.

Research the full VMware specification before implementing. Run the verifier with:

```sh
python3 tests/verify.py
```
