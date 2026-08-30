# vSAN Data Protection protection group snapshot client

A single-file Java client that creates a vSAN Data Protection protection group snapshot on a
VCF 9.1 snapshot appliance, waits for the asynchronous task to reach a terminal state, and
returns the resulting snapshot.

## Layout

| Path | Role |
| --- | --- |
| `src/SnapshotProtectionClient.java` | the client, the only file you edit |
| `docs/contract.json` | the wire contract, derived from the vSAN Data Protection OpenAPI document |
| `docs/official_sources.json` | the specification path, repository commit sha and operationIds behind the contract |
| `harness/MockSnapserviceServer.java` | in-process appliance, routed strictly from `docs/contract.json` |
| `harness/TestMain.java` | scenario driver and wire-shape verifier |
| `harness/Json.java` | JSON reader/writer used by the harness |
| `verify.sh` | compiles everything and runs the harness |
| `build/requests.log` | one JSON object per request, written by the appliance while the harness runs |

## Running

```sh
./verify.sh
```

The harness injects a deterministic in-process JDK `HttpClient` appliance, so verification
opens no socket. Nothing outside the process is contacted, and no credentials are needed.

## Reading the request log

After a run, `build/requests.log` holds one JSON object per request:

```json
{"seq":1,"method":"POST","path":"/api/snapservice/...","query":"vmw-task=true","operation_id":"...","status":202,"headers":{...},"body":"{...}"}
```

`operation_id` is `null` when a request matched no operation in `docs/contract.json`, which is
how an off-contract call shows up.
