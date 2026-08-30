# VCF NSX Policy realization module

Implement `src/VcfNsxPolicy/VcfNsxPolicy.psm1`. The manifest already pins the
preinstalled `VMware.Sdk.Vcf.SddcManager` PowerCLI prerequisite. Do not download,
install, copy, or vendor VMware modules.

The focused REST contract in `docs/contract.json` was extracted from the VCF 9.1
NSX Policy OpenAPI 2.0 specification. Its immutable repository provenance is in
`docs/official_sources.json`. The full upstream specification is intentionally
not vendored.

The module must export these commands:

* `New-VcfNsxPolicyClient`
  * `-Connection` accepts an authenticated
    `VMware.Sdk.OpenApi.Cmdlets.IServerConnection` and reuses its `HttpClient`.
    `-Server` may override the connection's server URI when the NSX Policy
    endpoint is different.
  * For non-interactive automation and the loopback verifier, the other parameter
    set accepts `-Server` and `-AccessToken`.
  * `-SkipCertificateCheck` is permitted only for the token parameter set.
* `Get-VcfNsxPolicySegment -Client <client>`
  * follows the contract cursor until all pages are read;
  * returns segment objects locally sorted by `display_name`, then `id`, both
    ascending. Never trust collection order returned by the service.
* `Set-VcfNsxPolicySegment -Client <client> -SegmentId <id> -DisplayName <name>
  [-ConnectivityPath <path>] [-TransportZonePath <path>]
  [-TimeoutSeconds <positive integer>] [-PollIntervalMilliseconds <nonnegative integer>]`
  * sends the contract PATCH with a `Segment` JSON body;
  * polls `ReadIntentStatus` for `/infra/segments/<id>`;
  * returns an object containing `SegmentId`, `IntentPath`, `Status`, and
    `PollCount` only when consolidated status is `SUCCESS`;
  * throws on consolidated status `ERROR` or when the timeout expires.

URI path components and query values must be escaped. Send JSON as
`application/json`, request JSON responses, and surface non-success HTTP status
codes as terminating errors.

Run the acceptance check with:

```text
python3 tests/verify.py
```

The check starts a loopback-only NSX Policy mock derived from the supplied
contract. It never contacts a live VMware endpoint.

The upstream `vmware/vcf-api-specs` repository and its extracted facts are
licensed under Apache-2.0. See `docs/official_sources.json` for the exact source
commit and file.
