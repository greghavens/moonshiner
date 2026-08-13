# VCF Operations 9.1 multi-step change

A dependency-free Go package that applies a four-step configuration change to a
VMware Cloud Foundation Operations 9.1 appliance and reports what the appliance
actually accepted.

The change onboards a monitored workload group:

1. `acquireToken` — authenticate against the appliance.
2. `createCustomGroup` — create the custom group.
3. `assignPolicy` — assign a policy to the group the appliance just created.
4. `createNotificationPluginRule` — create the notification rule for it.

The appliance has no transaction spanning the four operations. A step that fails
leaves the earlier steps applied, and the report is the only record of what
happened.

## Layout

| Path                         | Role                                                                |
| ---------------------------- | ------------------------------------------------------------------- |
| `opsrollout/rollout.go`      | The package. This is the implementation file.                        |
| `docs/contract.json`         | The REST contract the package must satisfy.                          |
| `docs/official_sources.json` | Provenance of the contract: spec path, revision, and operation ids.  |
| `internal/opsmock/`          | Loopback appliance mock, with a request log.                         |
| `verifier/wire_test.go`      | Checks the package against the contract.                             |

`docs/`, `internal/opsmock/`, `verifier/` and `go.mod` are fixtures and must not
be changed.

## Contract

`docs/contract.json` is derived from `vcf-operations-openapi.json` in the
`vmware/vcf-api-specs` repository at the revision named in
`docs/official_sources.json`. Its `operations` and `schemas` sections come
straight from that specification. Its `changeSequence` and `clientRules`
sections say how this package must apply those operations: the transport
headers, the rule for omitting a field the caller left unset, the rule for
sending a required field whose value is a zero value, and what the report must
say about each step after a failure.

## Running the checks

```sh
go test -race ./verifier/...
```

The mock listens on 127.0.0.1 and serves only the four operations above. No
VMware endpoint is contacted.
