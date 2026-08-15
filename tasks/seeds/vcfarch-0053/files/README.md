# Cedar Rapids VCF architecture package

The protected inputs under `fixtures/`, `schemas/`, and `specifications/` are
the design contract. The implementation belongs in `src/vcf_architecture` and
must use only the Python standard library.

Run the package from this directory with:

```sh
PYTHONPATH=src python3 -m vcf_architecture --output artifacts
```

Run acceptance verification with:

```sh
python3 tests/verify_architecture.py
```

