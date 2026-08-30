# vcfrotate

Rotation job for the vCenter automation service account in a VMware Cloud
Foundation 9.0 estate.  The account it rotates is the same account that has work
open against vCenter, so the job has to hand the workload from the retiring
session to the incoming one without abandoning anything that is already in
flight.

## Layout

| Path                        | What it is                                                        |
| --------------------------- | ----------------------------------------------------------------- |
| `vcfrotate/`                | the package, run as `python3 -m vcfrotate`                         |
| `docs/contract.json`        | the pinned projection of the vSphere Automation API specification  |
| `docs/official_sources.json`| where that projection came from, down to the commit                |
| `mock_vcenter.py`           | contract-pinned loopback endpoint used by the acceptance harness   |
| `verify.py`                 | the acceptance harness                                             |

`docs/`, `mock_vcenter.py`, `verify.py` and `.gitignore` are fixtures.

## Acceptance

```
python3 -B verify.py
```

The harness starts `mock_vcenter.py` on `127.0.0.1`, drives the job with dummy
credentials, and reads the mock's request log. It covers clean rotation, all
three documented abort reasons, failed-work report shapes, and the configured
worker ceiling. No live VMware endpoint is contacted.
