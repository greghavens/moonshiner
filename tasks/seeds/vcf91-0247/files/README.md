# vSAN Data Protection protection group snapshots (VCF 9.1)

A standard-library Go client for the VMware Cloud Foundation 9.1 vSAN Data
Protection Snapshot Appliance. Taking a protection group snapshot is an
asynchronous operation: the appliance answers the create request with a task
identifier, and the snapshot does not exist until that task reaches a terminal
status.

## Layout

| Path | Role |
| --- | --- |
| `docs/contract.json` | The pinned projection of the OpenAPI specification. Protected. |
| `docs/official_sources.json` | Where that specification came from. Protected. |
| `internal/mockappliance/mock.go` | Contract-pinned loopback appliance with a request log. Protected. |
| `verify/verify_test.go` | Table-driven verifier. Protected. |
| `.moonshiner/verify.sh` | Verifier entry point. Protected. |
| `snapservice/snapservice.go` | Types, defaults and error kinds. Protected. |
| `snapservice/client.go` | The implementation. **This is the file to edit.** |

## Source of the contract

`docs/contract.json` is derived from
`specifications/vsan-data-protection/vsan-data-protection-openapi.yaml` in the
Apache-2.0 [`vmware/vcf-api-specs`](https://github.com/vmware/vcf-api-specs)
repository, at the commit recorded in `docs/official_sources.json`. It comes
from the specification document itself, not from a rendered documentation page.

Two operationIds are in scope, and the appliance serves nothing else:

- `Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task`
- `Snapservice.Tasks_get`

## Verifying

```sh
sh .moonshiner/verify.sh
```

The verifier starts the contract-pinned appliance on an ephemeral `127.0.0.1`
port and reads its request log. No live VMware endpoint is contacted.
