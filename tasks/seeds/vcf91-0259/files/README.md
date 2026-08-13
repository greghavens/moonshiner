# vcfops-report

A standard-library-only Python package that drives the report-generation
workflow of the **VCF Operations** API in VMware Cloud Foundation 9.1.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The wire contract. Derived from the VCF Operations OpenAPI document; the authority for paths, methods, headers, request bodies and polling rules. |
| `docs/official_sources.json` | Provenance for the contract: repository, spec path, commit sha, and every operationId with its location in the spec. |
| `src/vcfops_report/client.py` | **Your work.** All methods are stubs. |
| `src/vcfops_report/cli.py` | **Your work.** All functions are stubs. |
| `src/vcfops_report/errors.py` | Implemented. The exception types to raise. |
| `src/vcfops_report/models.py` | Implemented. `ReportResult`. |
| `mock/vcfops_mock.py` | Loopback mock, pinned to `docs/contract.json`. Fixture. |
| `tests/` | The verifier. Fixture. |
| `verify.sh` | Runs the verifier. |

`docs/`, `mock/`, `tests/` and `verify.sh` are fixtures — read them, do not
change them. Everything you need to write lives under `src/vcfops_report/`.

## Running the mock by hand

```sh
python3 mock/vcfops_mock.py --log /tmp/requests.jsonl
```

It binds `127.0.0.1` on a free port and prints one readiness line:

```
VCFOPS_MOCK_READY {"port": 41234, "baseUrl": "http://127.0.0.1:41234/suite-api", ...}
```

It builds its route table from `docs/contract.json`, so it serves exactly the
five operations the contract names. Anything else answers `404`, and a known
path with the wrong method answers `405`.

It also enforces the contract's calling rules and explains every rejection in
the response body, for example:

```json
{"message": "optional property 'name' is unset and must be omitted from the request body entirely rather than sent as null",
 "httpStatusCode": 400, "contractViolation": "empty_optional_sent"}
```

Every request it receives — accepted or rejected — is appended to the JSON Lines
log passed to `--log`, with the method, raw path, query string, headers and raw
body exactly as they arrived.

### Fixture data

Credentials: user `report-runner`, password `Fixture-Passw0rd!`, auth source
`Local Users` (optional).

Resources: `8b1d4a76-2c33-4a5e-9f27-6a4f2c0b7e11` (a cluster) and
`3d9c7e21-5b48-4d19-8a63-1f7e5c9d0a22` (a datastore).

Report definitions, which differ in how generation ends:

| Report definition id | Generation ends |
| --- | --- |
| `2f7a2f2a-0001-4a10-9f1a-9b0f0d5c1001` | `COMPLETED` |
| `2f7a2f2a-0002-4a10-9f1a-9b0f0d5c1002` | `FAILED` |
| `2f7a2f2a-0003-4a10-9f1a-9b0f0d5c1003` | never reaches a terminal status |

`createReport` always returns `QUEUED`. The status advances only as `getReport`
is called: the first poll observes `QUEUED`, the second `RUNNING`, and the third
observes the outcome above.

## Verifying

```sh
./verify.sh
```
