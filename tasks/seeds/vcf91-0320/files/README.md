# vcfa-storage-profile

Reconciles VCF Automation vSphere storage classes from checked-in definitions.
Targets VCF Automation in VMware Cloud Foundation 9.1, the successor to
vRealize Automation and Aria Automation.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The wire contract. Hand-transcribed from the Broadcom xAPIs reference pages, because VCF Automation publishes no machine-readable specification. Authority for paths, methods, headers, query parameters, body fields and the placement precheck. |
| `docs/official_sources.json` | Provenance: every reference page URL, the operation it documents, and the date it was fetched. |
| `src/vcfa_storage/client.py` | The client. Currently a stub. |
| `src/vcfa_storage/errors.py` | Exception hierarchy. |
| `tests/vcfa_mock.py` | Loopback mock. Builds its routing table from `docs/contract.json`, so it serves only the three operations the contract names and answers 501 to anything else. Logs every request it receives as JSONL. |
| `tests/fixtures.json` | Static region and datastore inventory for the mock. |
| `tests/test_contract.py` | The verifier. |

## Contract status

`docs/contract.json` is **reference-derived**, not specification-derived. VCF
Automation has no entry in the `vmware/vcf-api-specs` repository and the
Developer Portal serves no OpenAPI document for this API set, so the contract
is a careful reading of human-readable documentation. Each section is marked
`literal` (stated on the cited page) or `inferred` (a reasonable reading the
page does not state outright). Read it before changing the client.

## Verifying

```
bash run_verification.sh
```

Deterministic and offline. The mock binds `127.0.0.1` on an ephemeral port and
has no clock and no randomness, so repeated runs are byte-identical. No live
VMware endpoint is contacted.
