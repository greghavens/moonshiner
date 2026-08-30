# Application onboarding for VCF Operations for Networks 9.1

Tooling the platform team uses to push application definitions from the CMDB
into VCF Operations for Networks (the VCF 9.1 successor to vRealize Network
Insight).

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The wire contract for the API operations this repository uses, derived from the upstream OpenAPI document. Authoritative. |
| `docs/official_sources.json` | Where that contract came from: repository, path, revision, checksum and the operation IDs used. |
| `onboarding/applications.json` | The CMDB export to onboard. |
| `mock/vcfops_networks_mock.py` | A loopback stand-in for an appliance. Routes and validates purely from `docs/contract.json`, serves nothing the contract does not name, and records every request it receives as JSON Lines. |
| `mock/fixtures/appliance-state.json` | The state that stand-in starts from: credentials, the tokens it hands out, when it ends the first session, the applications it already holds. |
| `scripts/setup.sh` | Installs the VCF 9.1 PowerCLI prerequisites from the PowerShell Gallery. |
| `tests/verify.py` | Acceptance test. Starts the stand-in on an ephemeral loopback port, drives the module against it once, and asserts the exact shape of every request that came out. |
| `src/` | The module itself. |

## Deployment prerequisite

Importing the finished manifest in a deployment environment requires the VCF
PowerCLI modules:

```sh
bash scripts/setup.sh
```

The `VMware.Sdk.Vcf.*` modules come from the PowerShell Gallery and are treated
as an environment prerequisite. They are never vendored into this repository.

## Running the tests

```sh
python3 tests/verify.py
```

Everything runs against `127.0.0.1`. No live appliance is contacted.
The acceptance test needs PowerShell 7 and Python 3.8+, but it does not download
the deployment prerequisite or require external network access.

## Working with the contract

`docs/contract.json` is generated from the upstream specification and is not
hand-edited. It carries, for each operation, the method and path, the query and
header parameters, the request body schema and the response schemas, plus the
transitive closure of every component schema those reference. The stand-in
reads the same file, so a request the contract does not describe is a 404 or a
400 rather than a silent success.
