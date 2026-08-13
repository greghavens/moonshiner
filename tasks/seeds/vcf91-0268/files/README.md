# opssync

A Go client for the VMware Cloud Foundation Operations API.

```
docs/       contract and provenance documents (you create these)
opsapi/     shared request and response types (fixed)
mock/       loopback stand-in for a VCF Operations appliance (fixed)
verify/     acceptance tests (fixed)
vcfops/     the client (yours)
```

Run everything with:

```
go test -race ./...
```
