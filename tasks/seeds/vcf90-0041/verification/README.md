# vcfsizer

Right-sizing automation for VMware Cloud Foundation 9.0 vCenter fleets.

An operator writes a plan describing the CPU and memory each virtual machine
should end up with, and this tool applies the plan through the vSphere
Automation API for vCenter.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The frozen subset of the vSphere Automation API this tool is allowed to call, transcribed from the VCF 9.0 OpenAPI specification. This is the authority for paths, parameter serialization, property names and status codes. |
| `docs/official_sources.json` | Provenance for the contract: repository, tag, commit sha, file path and the operation ids that were transcribed. |
| `fixtures/rightsizing-plan.json` | The plan the tool consumes. |
| `fixtures/vcenter_state.json` | Seed state for the loopback mock: credentials, inventory and the session-token policy. |
| `mock/vcenter_mock.py` | A loopback stand-in for a vCenter appliance, pinned to `docs/contract.json`. It serves only the operations the contract names and writes a JSON Lines request log plus a state snapshot. |
| `tests/test_wire_contract.py` | Verifies the exact wire shape of every request the tool makes. |
| `vcfsizer/` | The tool itself. |

## Running the mock by hand

```
python3 mock/vcenter_mock.py \
    --log /tmp/requests.jsonl \
    --state-out /tmp/state.json \
    --port-file /tmp/port
```

It prints `LISTENING <port>` once it is accepting connections on 127.0.0.1.
Every start reloads `fixtures/vcenter_state.json`, so runs are repeatable.
The request log records the method, path, raw query string, parsed query,
headers, raw body and response status of each request.

## Running the tests

```
python3 -m unittest discover -s tests -t . -v
```

The suite starts the mock itself on an ephemeral loopback port, runs the tool
against it once and then inspects the request log. No live VMware endpoint is
involved.
