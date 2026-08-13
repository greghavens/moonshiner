# VCF 9.1 NSX Policy client

Implement `nsxpolicy/client.go` as described by the task. The small contract in
`docs/contract.json` was extracted from the pinned VCF 9.1 NSX Policy OpenAPI
2.0 specification; provenance is recorded in `docs/official_sources.json`.

The repository is hermetic. `./verify.sh` runs table-driven tests with the race
detector against a loopback-only contract mock. It never contacts an NSX
Manager or any other live VMware service.
