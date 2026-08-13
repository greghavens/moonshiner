# SDDC-51782 — credential rotation failure diagnostics

Start with [`docs/incident.md`](docs/incident.md).

```
docs/incident.md          the ticket: what to build and what to produce
docs/contract.json        the SDDC Manager operations available, derived from the
                          VCF 9.0.0.0 OpenAPI specification
docs/official_sources.json provenance for the contract (repo, tag, commit, spec path)
src/VcfDiagnostics.java   the client to implement  <-- your work goes here
test/TestMain.java        harness that drives the client
mock/vcf_mock.py          loopback mock of SDDC Manager, pinned to the contract
mock/fixtures.json        the state the mock serves
mock/runtime/             per-run scratch: chosen port and requests.jsonl
verify/verify.py          asserts the exact request wire shape and the diagnosis
out/diagnosis.json        what the client writes
run_tests.sh              start mock -> compile -> run harness -> verify
```

Run everything with:

```sh
./run_tests.sh
```

The mock binds `127.0.0.1` on an ephemeral port. Nothing in this project contacts
a live VMware endpoint.

`mock/`, `test/`, `verify/` and `docs/` describe the environment you are working
against — implement `src/VcfDiagnostics.java` rather than changing them.
