# VCF Automation deployment client fixture

This repository contains a small, standard-library-only Python client and a
loopback fixture for the VCF Automation deployment update operation documented
in `docs/contract.json`.

Run the acceptance suite with:

```sh
python3 -m unittest discover -s tests -v
```

The tests never connect to a live VMware endpoint.
