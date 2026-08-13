# vcflcm

A dependency-free Python integration that drives a vCenter migration based
upgrade through the vSphere Automation API shipped with VMware Cloud
Foundation 9.0.

```
docs/contract.json           REST contract used by the package and the mock
docs/official_sources.json   provenance of the contract (repo, tag, commit, operationIds)
src/vcflcm/contract.py       contract loader
src/vcflcm/errors.py         exception types
src/vcflcm/client.py         HTTP transport for the four contract operations
src/vcflcm/upgrade.py        apply-and-poll driver
tests/mock_vcenter.py        loopback mock pinned to the contract, with a request log
tests/run_scenarios.py       scenario runner and wire-shape assertions
tests/verify.py              entry point: python3 tests/verify.py
```

The contract is derived from `specifications/vsphere/openapi/automation/vcenter.yaml`
at tag `9.0.0.0` of https://github.com/vmware/vcf-api-specs (Apache-2.0). The
operations it names are:

| operationId | request |
| --- | --- |
| `Vcenter.Lcm.Deployment.MigrationUpgrade_get` | `GET /api/vcenter/lcm/deployment/migration-upgrade` |
| `Vcenter.Lcm.Deployment.MigrationUpgrade_apply` | `POST /api/vcenter/lcm/deployment/migration-upgrade?action=apply` |
| `Vcenter.Lcm.Deployment.MigrationUpgrade.Status_get` | `GET /api/vcenter/lcm/deployment/migration-upgrade/status` |
| `Vcenter.Lcm.Deployment.MigrationUpgrade_cancel` | `POST /api/vcenter/lcm/deployment/migration-upgrade?action=cancel` |

`docs/` and `tests/` are protected fixtures. Only `src/` is implementation.

The mock listens on `127.0.0.1` and serves nothing beyond the four operations
above, so nothing in this repository talks to a real appliance.
