# vcfauto

A Go client for the VCF Automation deployment APIs in VMware Cloud Foundation 9.1.

VCF Automation is the successor to vRealize Automation / Aria Automation. Its
deployment APIs are reached under the `/deployment/api/` prefix on the VCF
Automation appliance and are authenticated with a bearer token.

## Layout

| Path                         | Purpose                                                  |
| ---------------------------- | -------------------------------------------------------- |
| `docs/contract.json`         | The API contract this package is pinned to.               |
| `docs/official_sources.json` | Provenance for every page the contract was derived from.  |
| `verify/`                    | Acceptance tests. Do not edit.                            |

## Tests

```
go test -race ./...
```

The package has no third-party dependencies and its tests must not contact any
network host outside the loopback interface.
