# wld01 capacity incident — VCF Operations 9.1 diagnostic client

Workload domain `wld01` runs on VMware Cloud Foundation 9.1. Platform operations keeps a
small Java client for pulling incident evidence out of the VCF Operations suite-api so that
post-incident write-ups quote what the platform actually recorded instead of what somebody
remembered.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The wire contract. Derived from the vmware/vcf-api-specs OpenAPI document; authoritative for this exercise. |
| `docs/official_sources.json` | Provenance: spec path, pinned commit sha, and the JSON pointer for every operationId. |
| `src/VcfOpsClient.java` | The client. One file, JDK standard library only. |
| `harness/TestMain.java` | Test harness. Compiles against the client's fixed signatures and drives the incident walk. |
| `mock/vcf_ops_mock.py` | Loopback mock of exactly the seven contracted operations, with a request log. |
| `verify/run_verify.py` | Deterministic verification, including the exact request wire shape. |

## Running things

```sh
# everything at once (compiles, starts the mock, runs the harness, checks the wire)
python3 verify/run_verify.py

# or drive the mock by hand
python3 mock/vcf_ops_mock.py --logdir .work --port 0 --portfile .work/port
javac -d .work/classes src/VcfOpsClient.java harness/TestMain.java
java -cp .work/classes TestMain "http://127.0.0.1:$(cat .work/port)/suite-api" .work/evidence.json
```

The mock appends one JSON object per request to `<logdir>/requests.jsonl` (method, path, raw
query string, parsed query, headers, raw and parsed body, status) and writes the identifiers it
minted for the run to `<logdir>/state.json`. Those identifiers are regenerated on every start.

`verify/run_verify.py` never contacts a VMware endpoint; the mock binds 127.0.0.1 only.

## Protected files

`docs/contract.json`, `docs/official_sources.json`, `mock/vcf_ops_mock.py` and
`harness/TestMain.java` are checksummed by the verifier against
`verify/protected_manifest.json`. Editing any of them fails verification.
