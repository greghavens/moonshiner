# vsan-dp-sweep

Takes a manual snapshot of every protection group on a vSAN Data Protection
cluster (VMware Cloud Foundation 9.1) and follows each resulting task to
completion.

## Layout

| path | state |
| --- | --- |
| `docs/contract.json` | **to be written** — the machine-readable wire contract |
| `docs/official_sources.json` | **to be written** — provenance of the contract |
| `vsandp/` | **to be written** — stdlib-only client and CLI |
| `mock/snapservice_mock.py` | provided — loopback appliance, routed by `docs/contract.json` |
| `mock/fixtures/site.json` | provided — inventory and behaviour of the simulated appliance |
| `tests/test_wire_contract.py` | provided — verification, do not edit |
| `verify.sh` | provided — runs the verification |

## The appliance simulator

The mock owns no routes of its own. It reads `docs/contract.json`, maps each
operation to the role that the contract assigns it, and serves nothing else; an
operation whose `role` it does not recognise is a fatal startup error. It binds
to 127.0.0.1 and appends one JSON object per request to the log named by
`--log`, which is what the verification reads.

```sh
python3 mock/snapservice_mock.py \
    --contract docs/contract.json \
    --fixtures mock/fixtures/site.json \
    --log /tmp/requests.jsonl \
    --port 8443
```

## Verification

```sh
./verify.sh
```
