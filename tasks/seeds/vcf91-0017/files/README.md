# VCF 9.1 landing-zone change

Implement `apply_landing_zone_change` in
`vcf91_change/client.py`. The public import is:

```python
from vcf91_change import apply_landing_zone_change
```

The full behavioral contract is in the task prompt. The REST subset in
`docs/contract.json` was projected from the official, commit-pinned VMware
Cloud Foundation 9.1 SDDC Manager OpenAPI specification. The implementation
must use only the Python standard library.

Run the acceptance verifier with:

```console
python3 -B .moonshiner/verify.py
```

The verifier uses only an ephemeral loopback server. It does not contact a
VMware installation or any other network service.
