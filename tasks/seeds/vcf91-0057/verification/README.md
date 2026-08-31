# VCF NSX Policy realization module

Implement `src/VcfNsxPolicy/VcfNsxPolicy.psm1`. The manifest already pins the
preinstalled `VMware.Sdk.Nsx.Policy.Infra` PowerCLI prerequisite. Do not download,
install, copy, or vendor VMware modules.

The focused REST contract in `docs/contract.json` was extracted from the VCF 9.1
NSX Policy OpenAPI 2.0 specification. Its immutable repository provenance is in
`docs/official_sources.json`. The full upstream specification is intentionally
not vendored.

The module must export these commands:

* `New-VcfNsxPolicyClient`
  * `-Connection` accepts an authenticated
    `VMware.Sdk.Nsx.Policy.Types.NsxServer` and preserves it for the
    generated NSX Policy commands.
* `Get-VcfNsxPolicySegment -Client <client>`
  * invokes `Invoke-ListAllInfraSegments` and follows its cursor until all pages
    are read;
  * returns segment objects locally sorted by `display_name`, then `id`, both
    ascending. Never trust collection order returned by the service.
* `Set-VcfNsxPolicySegment -Client <client> -SegmentId <id> -DisplayName <name>
  [-ConnectivityPath <path>] [-TransportZonePath <path>]
  [-TimeoutSeconds <positive integer>] [-PollIntervalMilliseconds <nonnegative integer>]`
  * invokes `Invoke-PatchInfraSegment` with the generated `Segment` model;
  * polls `Invoke-ReadIntentStatus` for `/infra/segments/<id>`;
  * returns an object containing `SegmentId`, `IntentPath`, `Status`, and
    `PollCount` only when consolidated status is `SUCCESS`;
  * throws on consolidated status `ERROR` or when the timeout expires.

Pass the caller-owned connection through each generated command's `-Server`
parameter. Do not replace the generated commands with a raw HTTP transport.

Run the acceptance check with:

```text
python3 tests/verify.py
```

The check creates an authenticated PowerCLI connection to a loopback-only NSX
Policy endpoint and verifies the requests produced by the generated commands.

The upstream `vmware/vcf-api-specs` repository and its extracted facts are
licensed under Apache-2.0. See `docs/official_sources.json` for the exact source
commit and file.
