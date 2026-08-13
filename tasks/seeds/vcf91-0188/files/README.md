# VCF Operations failure diagnostics

This fixture contains a small Go client for the VMware Cloud Foundation 9.1
Log Management search API. The client must correlate a deployment error log
with its VCF event before reporting a root cause.

The checked-in contract is an intentionally minimal extraction of the official
OpenAPI document. Tests run against `internal/vcfmock`, an HTTP server bound by
`httptest` to loopback and restricted to the operation in that contract.

Run the acceptance suite with:

```sh
go test -race ./...
```
