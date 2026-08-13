# vcfops — adapter instance collection triage

A small Go module that diagnoses a stalled VCF Operations adapter instance from
the records VCF Operations already holds.

```
docs/contract.json           projection of the VCF Operations OpenAPI specification (protected)
docs/official_sources.json   the specification revision the projection came from (protected)
docs/incident.md             the incident being triaged (protected)
opsdiag/                     the package under development
internal/opsmock/            loopback server pinned to docs/contract.json (protected)
verification/                protected acceptance suite
```

Run everything:

```sh
sh verification/run.sh
```

The module has no dependencies and contacts no VMware endpoint. Every request
made by the tests terminates on a loopback `httptest` server that refuses to
route anything `docs/contract.json` does not name and records the exact wire
shape of what it received.
