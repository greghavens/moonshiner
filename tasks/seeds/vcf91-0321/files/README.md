# vcfa-rotate

A standard-library-only Python package that rotates the secret behind a **VCF Automation**
named credential in VMware Cloud Foundation 9.1.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The API contract this package speaks. Read it first. |
| `docs/official_sources.json` | The source pages used to derive the contract. |
| `vcfa_mock.py` | Loopback mock of the contract. Fixture — do not modify. |
| `tests/test_rotation_contract.py` | The verifier. Do not modify. |
| `vcfa_rotate/client.py` | HTTP client. **Yours to implement.** |
| `vcfa_rotate/rotation.py` | The rotation. **Yours to implement.** |

## The contract is derived, not published

VCF Automation is the VCF 9.1 successor to vRealize / Aria Automation. Unlike most VCF
components it ships **no machine-readable API specification** — there is nothing for it in
the `vmware/vcf-api-specs` repository and Broadcom publishes no downloadable OpenAPI
document for these operations. `docs/contract.json` was therefore transcribed by hand from
the human-readable xAPIs reference pages, and `docs/official_sources.json` records the page
URL, the operation it documents, and the date it was fetched for every page read.

Practical consequence: the contract is authoritative *for this repository*, but it is
descriptive rather than normative. Where a reference page was silent, the contract carries
an explicit `notDocumented` note instead of a guess. Do not invent fields, parameters,
endpoints or error shapes to fill those gaps — the mock serves only what the contract names
and answers 404 to everything else.

## Running things

```sh
python3 -m unittest discover -s tests -v     # the verifier
python3 vcfa_mock.py --port 8443             # the mock, standalone, for poking at by hand
VCFA_MOCK_LOG=/tmp/vcfa.jsonl python3 vcfa_mock.py   # ...with a request log on disk
```

No network access is required or used: the mock binds to `127.0.0.1` and no live VMware
endpoint is contacted.
