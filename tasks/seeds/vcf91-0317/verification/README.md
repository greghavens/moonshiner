# vcfa-catalog-sync

An internal integration repository for VMware Cloud Foundation Automation 9.1.

VCF Automation is the successor to vRealize Automation and Aria Automation. Unlike
most of the VCF 9.1 surface it publishes **no machine-readable API specification** —
there is no document for it in the `vmware/vcf-api-specs` repository — so this
repository carries a hand-derived contract instead.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The API contract this repository codes against. Derived by reading the Broadcom xAPIs reference; it says so in its own `source_statement`. Clauses marked `pinned_by_contract` are places the reference is ambiguous and the contract chose one behaviour. |
| `docs/official_sources.json` | Provenance: every reference page URL that was read, the operation it documents, and the date it was fetched. |
| `mock/vcfa_mock.py` | A loopback HTTP mock pinned to `docs/contract.json`. It serves only the two operations the contract names and answers `404` for everything else. It writes a JSONL request log so tests can inspect the exact wire shape a client produced. |
| `mock/dataset.json` | The catalog fixture: 57 distinct items plus the order in which the server returns them. |
| `tests/test_contract.py` | The contract verifier. |
| `vcfa_catalog/` | The client package. |

`docs/`, `mock/` and `tests/` are fixtures. Treat them as read-only.

## Requirements

Python 3.9 or newer. Standard library only — no third-party packages, no network
access beyond loopback.

## Running the mock by hand

```
python3 mock/vcfa_mock.py --log /tmp/requests.jsonl --port 8888
```

It prints `READY <host> <port>` once it is listening, and appends one JSON object
per request to the log file. With `--port 0` it picks a free port, which is what
the tests do.

The mock accepts the bearer token `vcfa-test-token` unless `--token` says otherwise.

## Running the verifier

```
python3 -m unittest discover -s tests -t . -v
```

No live VMware endpoint is contacted at any point.
