# vcenter-content — content library item provisioning for VCF 9.0

A small automation helper the platform team uses to create vSphere Content
Library items on a VMware Cloud Foundation 9.0 vCenter. Standard library only:
this ships inside an appliance image where `pip install` is not an option.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The operations, headers and property rules this client is built against, derived from the vSphere Automation API OpenAPI document for vCenter. |
| `docs/official_sources.json` | Provenance for the above: repository, tag, commit and the operationIds used. |
| `mock/vcenter_mock.py` | A loopback stand-in for vCenter. Builds its routes from `docs/contract.json`, so it serves those operations and nothing else. Appends every request it receives to a JSONL log. |
| `tests/verify_contract.py` | Checks the provenance record and the exact wire shape the client produces. |
| `vcenter_content/` | The client. This is the part to write. |

`docs/`, `mock/` and `tests/` are read-only — build the client to fit them.

## Running

```
python tests/verify_contract.py
```

The mock can also be driven by hand while iterating; it prints the credentials
and library id it accepts, and writes the request log to `--log`:

```
python -m mock.vcenter_mock --port 8080 --log /tmp/requests.jsonl
```

`--create-faults N` controls how many create responses come back as a transient
`503` before one succeeds. The default of 1 reproduces the failure this client
exists to survive: the item is committed server-side and the response is lost.

## Contract notes

`Content.Library.ItemModel` has no JSON-Schema `required` list. Its create-time
rules are per property, and `docs/contract.json` records the classification
under `schemas."Content.Library.ItemModel".create` along with the sentence in
the source document each one came from.
