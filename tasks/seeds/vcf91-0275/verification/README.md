# VCF Operations report client

A single-file Java client for the report-generation flow of the VMware Cloud Foundation
Operations API (VCF 9.1), plus the offline harness that exercises it.

```
docs/contract.json          the wire contract, derived from the vmware/vcf-api-specs OpenAPI document
docs/official_sources.json  provenance: spec path, pinned commit sha, and every operationId used
src/VcfOpsReportClient.java the client   <- this is the file to implement
harness/mock_server.py      loopback mock, routed from docs/contract.json, writes a request log
harness/TestMain.java       drives the client through five scenarios, records what came back
harness/verify_wire.py      asserts the recorded requests against the contract
verify.sh                   mock -> compile -> run -> assert
```

Run everything with:

```
./verify.sh
```

`build/` holds the artifacts of a run: `requests.jsonl` (every request the mock saw, one
JSON object per line), `testmain.json` (what the client returned per scenario), plus the
compiler and process output.

## Notes

* The mock speaks HTTP/1.1 and binds `127.0.0.1` on an ephemeral port. Nothing in this
  tree reaches a VMware endpoint.
* `harness/`, `docs/` and `verify.sh` are fixtures; `src/VcfOpsReportClient.java` is the
  only file to change, and it must stay a single file using only the Java SE standard
  library.
* Reading `build/requests.jsonl` after a failed run is the fastest way to see what the
  client actually put on the wire.
