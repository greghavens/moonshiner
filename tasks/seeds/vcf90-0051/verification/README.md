# vCenter virtual-machine reconfiguration

A Go module that applies a reconfiguration change set to a virtual machine
through the VMware Cloud Foundation 9.0 vSphere Automation API for vCenter.

| Path | What it is |
| --- | --- |
| `vmreconfig/` | The package to implement. Currently a stub. |
| `docs/contract.json` | The wire contract, derived from the specification. Read-only. |
| `docs/official_sources.json` | Where that contract came from: repository, tag, commit, spec path, operationIds. Read-only. |
| `internal/contract/` | Loads `docs/contract.json`. Read-only. |
| `internal/vcmock/` | Loopback appliance on 127.0.0.1, routed from the contract, with a request log. Read-only. |
| `verify/` | Protected verification. Read-only. |
| `verify.sh` | `gofmt`, `go vet`, `go test -race ./...`. Read-only. |

Implement `vmreconfig` and add table-driven tests alongside it. Everything else
is fixed.

```
./verify.sh
```

No live VMware endpoint is contacted: every request goes to the loopback
appliance in `internal/vcmock`.
