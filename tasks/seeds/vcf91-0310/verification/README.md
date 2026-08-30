# VCF Automation 9.1 — retry-safe catalog provisioning

A PowerShell client for the VMware Cloud Foundation Automation 9.1 catalog, used
by the platform team's provisioning jobs. Those jobs are re-run by hand, by cron
and by whatever restarted them after a network blip, so the one mutating call
they make has to be safe to run again.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The wire contract for the three operations this client may use. Authoritative for methods, paths, parameter names, property names, types and authentication. |
| `docs/official_sources.json` | Every reference page the contract was transcribed from, which operation it documents, and when it was read. |
| `mock/vcfa_mock.py` | Loopback stand-in for the appliance. Pinned to the contract, serves nothing the contract does not name, logs every request. |
| `mock/fixtures/appliance-state.json` | Default starting state for the stand-in. |
| `scripts/setup.sh` | Installs the VCF PowerCLI SDK prerequisite. |
| `src/VcfAutomationCatalog/` | The module. |
| `tests/verify.py` | Acceptance test. Starts the stand-in for you on an ephemeral port. |

## Where the contract came from

VCF Automation is the VCF 9.1 successor to vRealize / Aria Automation. Unlike
the rest of the platform it publishes **no OpenAPI specification** — it is absent
from the `vmware/vcf-api-specs` repository, which is why the auto-generated VCF
PowerCLI SDK ships `VMware.Sdk.Vcf.SddcManager`, `.Ops`, `.Installer` and
`.CloudBuilder` but nothing for Automation. There is no generated binding to call
and no specification to generate one from.

`docs/contract.json` was therefore transcribed by hand from the xAPIs reference
pages on the Broadcom Developer Portal. It says so, in `sourceKind` and
`sourceStatement`, and `docs/official_sources.json` records the receipts. Read
both before writing a request: a reference page is rendered documentation, not a
specification, and in one place it contradicts itself — the contract flags that
rather than quietly picking a side.

## Running things

```sh
bash scripts/setup.sh          # once; installs the SDK prerequisite
python3 tests/verify.py        # the acceptance test
```

To poke at the stand-in by hand:

```sh
python3 mock/vcfa_mock.py --port 8721 \
    --state mock/fixtures/appliance-state.json \
    --state-out /tmp/state-out.json \
    --log /tmp/requests.jsonl
```

It prints `LISTENING <port>` and then serves on `127.0.0.1`. `--state-out` is
rewritten after every mutation, so it shows what the appliance ended up holding;
`--log` gets one JSON object per request, accepted or refused, including the
headers, the raw query string and the parsed body.

Copy the fixture and edit your copy to try things out — extra catalog items,
deployments that already exist, or a `faults` entry to make the stand-in
misbehave on purpose:

```json
"faults": [
  { "operation": "requestCatalogItemInstances",
    "catalogItemId": "a4c19e3b-7f28-4d16-9a05-c3e81b6d4207",
    "mode": "commit-then-503", "times": 1 }
]
```

`commit-then-503` creates the deployment and *then* answers 503, so the caller
never learns the id. `fail-before-commit` answers 503 having created nothing.

Nothing here talks to a live VMware endpoint.
