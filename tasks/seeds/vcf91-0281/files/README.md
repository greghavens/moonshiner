# Drain-safe credential rotation for VCF Operations 9.1

A dependency-free Java client that rotates an adapter credential in VMware
Cloud Foundation Operations without stranding in-flight collections on the
outgoing secret.

## Layout

| Path | What it is |
| --- | --- |
| `src/VcfOpsCredentialRotator.java` | the client — the only file you change |
| `docs/contract.json` | the request/response contract, derived from the pinned OpenAPI document |
| `docs/official_sources.json` | which specification, which commit, which operationIds |
| `.protected/mock_vcfops.py` | loopback mock of the contracted surface |
| `.protected/TestMain.java` | harness that drives the client and prints its result |
| `.protected/verify.py` | the verifier |

## Contract

`docs/contract.json` is derived from
`specifications/vcf-operations/vcf-operations-openapi.json` in
[vmware/vcf-api-specs](https://github.com/vmware/vcf-api-specs) at commit
`c3f3b52c845dd967cabbc21680e893292077d5ba` (VCF Operations API 9.1.0.0,
Apache-2.0). It is the source of truth for base path, authorization,
operations, and body shapes. `docs/official_sources.json` records the JSON
pointer into the specification behind every operation and every pinned fact.

Six operations are in play: `acquireToken`, `getCredential`,
`createCredential`, `getAdapterInstancesUsingCredential`,
`patchAdapterInstance`, `deleteCredential`.

## Running it

```
python3 -B .protected/verify.py
```

The verifier compiles the client with the harness and runs isolated successful-
drain and exhausted-drain rotations. Each scenario starts a fresh mock on
`127.0.0.1` on an ephemeral port and asserts the wire shape of every request
from its request log. Nothing outside loopback is contacted. A JDK 17 or later
and Python 3.8 or later are the only prerequisites.

The mock serves only the operations `docs/contract.json` names and answers
anything else with a 404 that says so. Its rejection messages are the fastest
way to find a wire-shape mistake: read them.
