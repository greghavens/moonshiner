# vcfa-batch

Submits a batch of catalog deployment requests to a VMware Cloud Foundation
Automation 9.1 appliance and follows each one until it reaches a terminal state.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The API contract this client must satisfy. Hand-derived from Broadcom's xAPIs reference documentation, **not** a published specification. |
| `docs/official_sources.json` | Every reference page the contract was derived from: URL, the operations it documents, and the date fetched. |
| `mock/vcfa_mock.py` | Loopback mock appliance. Builds its route table by reading `docs/contract.json`, so it serves only the operations the contract names and 404s everything else. Writes a JSONL request log. |
| `mock/fixtures.json` | Catalog items, credentials and request progressions the mock serves. |
| `inputs/batch.json` | The batch of deployments to submit. |
| `src/vcfa_client/` | The client. `__main__.py` is complete; `client.py` is a stub. |
| `tests/test_wire_contract.py` | Verifier. Runs the client against the mock and asserts the exact wire shape of every request. |

## Running

```
PYTHONPATH=src python -m vcfa_client --config CONFIG --batch inputs/batch.json --out results.json
```

`CONFIG` is JSON with `baseUrl`, `clientId`, `clientSecret`, `refreshToken` and
`projectId`. Exits 0 when every deployment reached a terminal state, 1 otherwise.

## Verifying

```
python3 -m unittest discover -s tests -t . -v
```

The verifier starts the mock on `127.0.0.1` on an ephemeral port and points the
client at it. Nothing contacts a live VMware endpoint. Standard library only, no
third-party packages.

## The contract is reference-derived

VCF Automation publishes no machine-readable API specification — it is absent
from `vmware/vcf-api-specs`. `docs/contract.json` was transcribed by hand from
the reference documentation on developer.broadcom.com. Where the reference is
silent, ambiguous, or self-contradictory, `docs/contract.json` records the gap in
its `reference_gaps` section instead of inventing an answer. Read those gaps
before assuming behaviour the contract does not state — notably, the reference
documents no error body for a 401, so expiry handling must key on the status code
alone.
