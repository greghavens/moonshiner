# VCF Operations log management client fixture

This repository contains a small, standard-library-only Python client fixture.
The checked-in contract is a focused OpenAPI subset for one VCF Operations 9.1
Log Management operation.

Implement the two `NotImplementedError` sites under `vcf_operations/`, then run:

```sh
python3 -B verify.py
```

The verification server binds only to an ephemeral loopback port. It deliberately
loses the first response after applying the PUT so retry behavior is exercised
without contacting a VMware deployment.
