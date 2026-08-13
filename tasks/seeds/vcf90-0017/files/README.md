# SDDC Manager host commissioning

A dependency-free Go module that commissions ESXi hosts into a VMware Cloud
Foundation 9.0 fleet through the SDDC Manager API.

```
docs/contract.json           the wire contract, extracted from the OpenAPI spec
docs/official_sources.json   which spec, which tag, which commit, which operations
hostcommission/              the package to implement (currently a stub)
internal/contract/           loads docs/contract.json
internal/smmock/             loopback SDDC Manager mock, pinned to the contract
verify/                      protected wire-shape verification
verify.sh                    gofmt, go vet, go test -race ./...
```

`docs/`, `internal/`, `verify/` and `verify.sh` are protected fixtures.
`hostcommission/` is the only place to write implementation code.

Everything runs against `internal/smmock`, which serves the five operations
`docs/contract.json` names on 127.0.0.1. No live VMware endpoint is contacted.
