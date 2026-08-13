# vcf-ops-networks-tools

PowerShell tooling for driving **VCF Operations for Networks** (VCF 9.1) — the
component that replaced vRealize Network Insight — from our platform automation.

## Layout

| Path | Purpose |
| --- | --- |
| `docs/contract.json` | The wire contract this repository targets, derived from the product OpenAPI specification. |
| `docs/official_sources.json` | Provenance for the contract: spec path, pinned commit sha, and every operationId used. |
| `src/VCFOpsNetworks/` | The PowerShell module. |
| `mock/ni_mock.py` | Loopback fixture pinned to `docs/contract.json`. Serves only the operations the contract names and logs every request. |
| `tests/` | Protected verification. |
| `scripts/setup.sh` | Checks the PowerShell and Python versions supplied by the harness. |

## Dependencies

The module uses PowerShell's built-in HTTP and JSON facilities and has no
third-party runtime dependency. A VCF Operations JWT accepted by
`-OpsAccessToken` is supplied by the caller.

## Running the checks

```sh
./scripts/setup.sh
python3 tests/verify.py
```

The fixture binds an ephemeral port on `127.0.0.1`. Nothing in the test path
contacts a VMware endpoint.

## Trying the fixture by hand

```sh
python3 mock/ni_mock.py --contract docs/contract.json --log /tmp/requests.jsonl \
  --port 0 --fail-tier db
```

It prints `PORT <n>` once bound. Every request lands in the log as one JSON
object per line, including the raw request body — which is how the wire shape
gets checked.
