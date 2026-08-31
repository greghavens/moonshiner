# VCF Operations failure diagnostics

This fixture contains a small Go client for the VMware Cloud Foundation 9.1
Log Management search API. The client must correlate a deployment error log
with its VCF event before reporting a root cause.

Run the acceptance suite with:

```sh
go test -race ./...
```
