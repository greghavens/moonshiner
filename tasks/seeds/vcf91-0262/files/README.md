# VCF Operations custom-group reconciler

Fleet automation for VMware Cloud Foundation 9.1 needs to make sure a named
custom group exists in **VCF Operations** before a rollout starts. The playbook
that calls it is retried — on a network blip, on a partial run, on an operator
re-running the step — so creating the group has to be safe to repeat.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The wire contract, derived from the VCF Operations OpenAPI specification. Source of truth for paths, methods, schemas, auth and serialization. |
| `docs/official_sources.json` | Provenance: repository, spec path, commit sha and the operationIds the contract uses. |
| `mock/vcfops_mock.py` | Loopback mock of the suite-api, routed from `docs/contract.json`. Records every request to a JSONL log. |
| `tests/test_wire_contract.py` | Wire-shape verification. Runs against the mock only. |
| `vcfops_customgroups/` | **To be written.** The client package. |

## Running the mock by hand

```sh
python3 mock/vcfops_mock.py --port 8443 --request-log ./requests.jsonl
```

## Verifying

```sh
python3 -m unittest tests.test_wire_contract -v
```
