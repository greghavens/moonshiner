# VCF Operations for Networks — application inventory collector

A small Go module that collects the application inventory from a **VCF Operations
for Networks** appliance (VMware Cloud Foundation 9.1; the successor to vRealize
Network Insight).

```
docs/contract.json           wire contract for the four operations in scope
docs/official_sources.json   which specification revision the contract came from
internal/opsnet/             the API client            <- implement this
internal/opsnetmock/         loopback mock of the contract (protected)
verify/                      acceptance suite (protected)
```

Run the suite:

```
go test -race ./...
```

`verify/` drives `internal/opsnet` against the loopback mock in
`internal/opsnetmock` and asserts the exact shape of every request the client
puts on the wire. Nothing here talks to a real appliance.
