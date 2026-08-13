# vcf/lcm — SDDC LCM component upgrade client

A small Go client for two operations of the VMware Cloud Foundation 9.1
SDDC LCM service.

| path | purpose |
| --- | --- |
| `docs/contract.json` | the pinned operation subset, derived from the official OpenAPI specification |
| `docs/official_sources.json` | spec path, repository commit sha and the operationIds used |
| `internal/contractmock/` | in-process HTTP fixture whose routes are built from `docs/contract.json`, with a request log |
| `verification/` | protected wire-shape checks |
| `sddclcm/` | the package to implement, plus its own table-driven tests |

Run everything with:

```
bash .moonshiner/verify.sh
```
