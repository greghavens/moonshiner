# VCF Automation catalog client

A single-file Java client for the Catalog service of **VCF Automation** in VMware Cloud Foundation
9.1, the successor to vRealize Automation and Aria Automation.

## Layout

| Path | Role |
| --- | --- |
| `src/VcfCatalogClient.java` | **The only file you edit.** Implement the client here. |
| `src/Json.java` | Supplied dependency-free JSON reader and writer. |
| `docs/contract.json` | The authoritative wire contract. Read this first. |
| `docs/official_sources.json` | Every reference page the contract was transcribed from, with URL, operation and fetch date. |
| `.protected/TestMain.java` | Verification harness. |
| `.protected/MockVcfAutomation.java` | Loopback mock, route table pinned to `docs/contract.json`. |
| `.protected/verify.sh` | Compiles everything and runs the harness. |

## Source of the contract

VCF Automation publishes no OpenAPI document. It is absent from VMware's Apache-2.0
`vmware/vcf-api-specs` repository, and the Broadcom Developer Portal offers no machine-readable
specification for the Catalog service. `docs/contract.json` was therefore transcribed by hand from
the rendered xAPIs reference pages listed in `docs/official_sources.json`, and it says so in its own
`source_statement`. It is a reading of documentation, not a projection of a specification. Where the
documentation is silent the contract records an explicit choice under `local_decisions`.

## Running the verifier

```
bash .protected/verify.sh
```

Requires a JDK providing `javac` and `java`. The harness builds its own catalog fixtures and bearer
token at runtime, starts the mock on an ephemeral `127.0.0.1` port, drives your client, and then
reads the mock's flushed JSONL request log to assert the exact wire shape of every request you sent.
No live VMware endpoint is contacted.
