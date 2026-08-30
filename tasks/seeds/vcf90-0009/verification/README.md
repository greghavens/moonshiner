# vcf-lifecycle-tools

Internal automation for our VMware Cloud Foundation 9.0 estate. Runs on the
jump hosts, which have a bare CPython install and no package index access, so
everything here is **standard library only** — no `requests`, no `pyVmomi`, no
vendored SDK.

## Layout

| Path | What it is |
| --- | --- |
| `sddcbundle/` | The client package. `client.py` holds the public surface; fill in the bodies. |
| `docs/` | Derived integration artifacts (`contract.json`, `official_sources.json`). |
| `tools/mock_sddc_manager.py` | Loopback mock of SDDC Manager, pinned to the contract. Serves only the contracted operations, rejects everything else, and appends every request it receives to a JSON Lines log. |
| `verify/verify_seed.py` | Acceptance check. Drives the package against the mock and asserts the exact wire shape of each request. |

`tools/` and `verify/` are fixtures — treat them as read-only.

## Running the checks

```
python3 verify/verify_seed.py
```

The verifier starts the mock on 127.0.0.1 on an ephemeral port; nothing outside
the machine is contacted.

## House rules for API integrations

* Every SDDC Manager operation we call is written down in `docs/contract.json`,
  keyed by the `operationId` from the published OpenAPI specification, and the
  exact specification revision we read is recorded in `docs/official_sources.json`.
  A contract entry that cannot be traced back to a pinned specification commit
  does not ship.
* Optional request fields that the caller did not set are **omitted** from the
  JSON body. We never send `null`, `""`, or an empty object to mean "not set" —
  SDDC Manager treats those as explicit input.
* Long-running operations return a task. We poll the task until it reports a
  terminal state. "Accepted" is not "done".
