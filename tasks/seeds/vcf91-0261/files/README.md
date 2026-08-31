# vcfops-alerts

Read the VMware Cloud Foundation 9.1 Operations alert collection over the
Operations API—not log management—and emit it completely, once per alert, in a
stable order. Use only the Python standard library.

## Deliverables

Research the pinned official OpenAPI specification identified in the task
prompt. From that source, create `docs/contract.json` and
`docs/official_sources.json`, then implement:

- `src/vcfops_alerts/contract.py`
- `src/vcfops_alerts/client.py`
- `src/vcfops_alerts/__main__.py`

`src/vcfops_alerts/errors.py` is already implemented. Do not modify the tests.

## Required behavior

- Build every request target from the authored contract rather than duplicating
  paths or query-parameter names in the client.
- Authenticate only operations whose researched security requirements call for
  it, and release the session after the collection read.
- Omit optional body properties and query parameters the caller did not set.
  Preserve explicitly supplied values. Encode array filters as repeated query
  parameters in caller order.
- Read pages from zero upward without stopping early or requesting beyond the
  collection described by the response pagination metadata.
- When an alert appears on more than one page, retain the row from its earliest
  page. Return each alert once, ordered by `startTimeUTC` descending and then
  `alertId` ascending.
- Raise the existing API exception for unusable responses; do not expose the
  password or session token.

## Public API

```python
from vcfops_alerts import OperationsClient, load_contract

contract = load_contract()

with OperationsClient(base_url, username=..., password=..., auth_source=None,
                      contract=None, timeout=30.0) as client:
    alerts = client.fetch_alerts(page_size=1000, resource_ids=None,
                                 alert_ids=None)
```

`base_url` is the appliance root. The command-line entry point accepts
`--base-url`, `--username`, `--password`, optional `--auth-source`,
`--page-size`, repeated `--resource-id`, and repeated `--alert-id`, and prints
the JSON alert array followed by a newline.

Run `python3 -B tests/verify.py` when finished.
