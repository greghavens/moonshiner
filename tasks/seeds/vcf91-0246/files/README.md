# VCF 9.1 vSAN Data Protection snapshot inventory

Implement `collect_vm_snapshot_inventory` in
`vsan_snapshot_inventory/client.py`. The public import is:

```python
from vsan_snapshot_inventory import SnapserviceError, collect_vm_snapshot_inventory
```

`docs/contract.json` is a projection of the two vSAN Data Protection operations
in scope, read out of the commit-pinned VMware Cloud Foundation 9.1 Snapshot
Appliance OpenAPI specification. `docs/official_sources.json` records the
repository, the Apache-2.0 license, the commit, the specification path and each
operationId. The behavioral contract is in the task prompt.

The implementation must use only the Python standard library.

Run the acceptance verifier with:

```console
python3 -B .moonshiner/verify.py
```

The verifier starts `tools/mock_snapservice.py` on an ephemeral 127.0.0.1 port,
generates its credentials, session token and snapshot inventory at runtime, and
reads the mock's request log to check the wire shape. It contacts no VMware
installation and no other network service.
