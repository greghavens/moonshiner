# vcfa-day2-gate

A small integration against **VCF Automation** in **VMware Cloud Foundation 9.1** (the successor to
vRealize Automation and VMware Aria Automation).

The job it does is narrow. An operator names a day-2 action on a deployment — power it off, snapshot
it — and this client decides whether that action may actually be run right now, and only then runs
it. Getting the order wrong means mutating an appliance on the strength of a guess.

## The two operations we speak

VCF Automation is **not** covered by the `vmware/vcf-api-specs` repository, so there is no OpenAPI
document to generate a client from. `docs/contract.json` is the project's own restatement of the API,
transcribed by hand from the VCF Automation xAPIs reference pages on `developer.broadcom.com`.
Every page consulted, the operation it documents and the date it was fetched are recorded in
`docs/official_sources.json`. Read `docs/contract.json` before writing code — it is the authority
here, and it is deliberately explicit about the two things the reference pages leave implicit:

- **`gating`** — when the mutating call may be sent at all.
- **`serialization`** — what may appear in the request body, and what must not.

| operationId | | |
| --- | --- | --- |
| `getDeploymentActions` | `GET /deployment/api/deployments/{deploymentId}/actions` | the precheck |
| `submitDeploymentActionRequest` | `POST /deployment/api/deployments/{deploymentId}/requests` | the only mutating call |

Both carry `Authorization: Bearer <JWT>`.

## Layout

```
docs/contract.json            the contract, derived from reference documentation
docs/official_sources.json    every source page, operation and fetch date
src/main/java/com/example/vcfa/VcfaDeploymentActionClient.java   <- yours
src/main/java/com/example/vcfa/Json.java                         provided; dependency-free JSON
test/MockVcfaServer.java      loopback appliance, routed from the contract
test/TestMain.java            scenario harness
test/Verifier.java            wire-shape assertions over the mock's request log
run_tests.sh                  compile and verify
```

Only `VcfaDeploymentActionClient.java` is yours to write. Everything under `test/`, both files under
`docs/`, `Json.java` and `run_tests.sh` are protected — the harness owns them, so leave them alone.
The client is a single file; keep it that way.

## The mock

`MockVcfaServer` binds `127.0.0.1` on an ephemeral port and builds its route table by reading the
`operations` array out of `docs/contract.json`. It therefore serves exactly the operations the
contract names: any other path is answered `404`, and a contract path reached with the wrong method
is answered `405`. It logs every request it receives — method, path, query, headers, raw body — to a
JSONL file that the harness reads back and asserts against.

## Running it

```
./run_tests.sh
```

Requires a JDK (17 or newer). No network access, no build tool, no dependencies to download.
