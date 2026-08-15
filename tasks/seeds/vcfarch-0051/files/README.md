# VCF 9.0 architecture generator

This repository is an implementation exercise. The protected inputs define a
single-site greenfield VCF 9.0 management domain and a separately versioned
estate that must be migrated into the new platform.

Run the completed package with:

```sh
python3 -B -m vcf_architect \
  --requirements fixtures/site-requirements.json \
  --estate fixtures/estate.json \
  --compatibility constraints/compatibility-snapshot.json \
  --output-dir artifacts
```

The command must write `artifacts/sddc-spec.json` and
`artifacts/migration-plan.json`. Research notes are authored separately as
`artifacts/research.json`; they are intentionally outside deterministic
verification.

Run acceptance verification with:

```sh
python3 -B .protected/verify.py
```

The installer OpenAPI document is vendored from tag `9.0.0.0`, commit
`85151f6b1bb58f13b6ac0304bfec53904bea085f`, of `vmware/vcf-api-specs`.
The upstream repository is licensed under Apache-2.0; its license is retained
at `specifications/LICENSE`.
