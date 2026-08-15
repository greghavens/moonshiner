# vcfarch

This workspace contains a brownfield VMware Cloud Foundation estate fixture,
a pinned compatibility authority, and protected acceptance checks. The missing
`vcfarch` package must turn those inputs into the machine-readable migration
architecture described in the task.

Run the finished planner with:

```text
python3 -m vcfarch \
  --inventory fixtures/estate_inventory.json \
  --compatibility compatibility/vcf-9.1-compatibility-snapshot.json \
  --output architecture/migration-plan.json
```

Run the acceptance checks with `./scripts/verify.sh`.

The checks are offline. Live Broadcom research is a design activity performed
with the harness's network tools and recorded separately in
`research/sources.json`; verification checks the record's structure but does
not replay the research or prescribe particular sources.
