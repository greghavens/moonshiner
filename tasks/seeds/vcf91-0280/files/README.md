# VCF Operations — adapter instance onboarding client

Onboarding an adapter instance into VCF Operations 9.1 is a gated two-step flow: test the
connection to the data source first, and create the adapter instance only if that test succeeded.
This project implements that flow as a single-file Java client.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The REST contract, derived from the VCF Operations OpenAPI specification |
| `docs/official_sources.json` | Where the contract came from: spec path, commit sha, operation ids |
| `src/OpsAdapterClient.java` | The client. **This is the file to implement.** |
| `harness/MockOpsServer.java` | Loopback stand-in for the appliance; pinned to `docs/contract.json` |
| `harness/TestMain.java` | Drives the client through six scenarios and prints one line each |
| `verify/verify.py` | Compiles, runs the harness, asserts the recorded wire shape |

`docs/`, `harness/` and `verify/` are fixed. Only `src/OpsAdapterClient.java` should change.

## Running it

```sh
python3 verify/verify.py
```

That compiles `src/` and `harness/` into `build/`, runs `TestMain` against a mock bound to
`127.0.0.1` on an ephemeral port, and checks `build/request-log.jsonl`. Nothing outside loopback is
contacted. A JDK 17 or newer and Python 3.9 or newer are the only requirements; there are no
third-party libraries on the classpath.

## The mock

`MockOpsServer` reads `docs/contract.json` at startup and serves only the operations the contract
names — anything else answers 404. It records every request it receives to
`build/request-log.jsonl` as JSON Lines: method, path, raw and parsed query, the `Authorization`,
`Content-Type` and `Accept` headers, the raw body, and the status it returned. `TestMain` writes a
marker line between scenarios so the log can be read scenario by scenario.

The fake appliance refuses a connection test whose `VCURL` resource identifier points at
`vcenter-down.lab.local`, and refuses to create an adapter instance named
`Duplicate VC Adapter Instance`.
