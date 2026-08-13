# vcfa-provision

A stdlib-only Python client for provisioning a virtual machine through the
**VCF Automation** provisioning service in VMware Cloud Foundation 9.1.

VCF Automation is the successor to vRealize Automation / Aria Automation. Machine
provisioning there is **asynchronous**: the create call returns a request tracker,
and the caller has to poll that tracker until it reaches a terminal state before
the provisioned machine can be read back.

This repository contains:

| Path | What it is |
| --- | --- |
| `vcfa_provision/` | the client package — `client.py` is unimplemented |
| `docs/` | where the derived API contract and its source list belong (missing) |
| `mock/server.py` | an in-process HTTP fixture of the provisioning service (**protected**) |
| `tests/` | the acceptance tests (**protected**) |
| `verify.sh` | integrity check + test run (**protected**) |

`mock/`, `tests/`, `verify.sh` and `PROTECTED.sha256` are protected: `verify.sh`
verifies their SHA-256 digests before running anything, and the run fails if any
of them changed.

## Running

```
./verify.sh
```

No network access is used by the tests. The mock intercepts the standard
library's HTTP connection at the socket boundary, parses the raw request bytes,
and records every request to a JSON Lines log that the tests read back.

## Client surface

The tests import exactly this surface. Names, argument names and defaults are fixed.

```python
from vcfa_provision import (
    VcfAutomationClient, ProvisionResult,
    MachineSpec, NetworkInterfaceSpec, DiskSpec, Constraint, Tag,
    ApiError, ProvisioningFailed, ProvisioningTimeout,
)

client = VcfAutomationClient(
    base_url,                 # e.g. "http://127.0.0.1:54321"
    token,                    # value sent in the Authorization header, verbatim
    api_version,              # value sent in the apiVersion query parameter
    poll_interval=5.0,        # seconds to wait between tracker polls
    max_poll_attempts=60,     # maximum number of tracker reads
    sleep=time.sleep,         # injected so tests do not really sleep
)

result = client.provision_machine(spec)   # -> ProvisionResult
```

`ProvisionResult` is a dataclass with the fields `request_id`, `machine_id`,
`machine` (the decoded machine document), `tracker` (the final decoded tracker
document) and `poll_count` (how many tracker reads were performed).

### Required behaviour of `provision_machine`

1. Submit the create-machine request with a JSON body built from `spec`.
   **Optional fields that are unset must be absent from the body** — not sent as
   `null`, `""`, `[]` or `{}`. This applies to nested objects too.
2. Every request carries the `Authorization` header set to `token` **exactly as
   given** (the client adds no prefix of its own) and the `apiVersion` query
   parameter set to `api_version`.
3. If the create call does not return its documented success status, raise
   `ApiError(status_code, body)` without polling.
4. Read the request id out of the returned tracker and poll the tracker
   operation. The first poll happens immediately — `sleep(poll_interval)` is
   called *between* polls only, never before the first one and never after the
   last one.
5. Stop as soon as the tracker reports a terminal state; do not poll again after
   that.
6. A terminal failure raises `ProvisioningFailed`; exhausting `max_poll_attempts`
   without reaching a terminal state raises `ProvisioningTimeout`. Neither may
   read the machine back.
7. On terminal success, the provisioned machine is identified by the first entry
   of the tracker's resource collection, which is a link whose last path segment
   is the machine id. Read that machine and return the `ProvisionResult`.
8. The client issues requests to the three operations named by the contract and
   to nothing else.

`ApiError`, `ProvisioningFailed` and `ProvisioningTimeout` are already defined in
`vcfa_provision/errors.py`; the dataclasses in `vcfa_provision/models.py` are
already written and their attribute names are fixed.

## `docs/contract.json`

The wire contract this client is pinned to, derived from the vendor's published
API reference. Shape:

```jsonc
{
  "contract_version": "1",
  "product": "...",             // product the contract describes
  "product_version": "...",     // "9.1"
  "source": {
    "type": "reference-documentation",
    "published_specification": false,
    "statement": "...",         // plain statement that this contract was derived from
                                // reference documentation and not from a published
                                // machine-readable API specification
    "sources_file": "docs/official_sources.json"
  },
  "security": {
    "scheme": "...",            // as documented by the service's security schema
    "in": "...",
    "name": "..."               // header name
  },
  "operations": [
    {
      "id": "createMachine",    // one of: createMachine, getRequestTracker, getMachine
      "summary": "...",
      "method": "...",
      "path": "...",            // documented path, with {id} placeholders
      "query_parameters": [
        {"name": "...", "type": "...", "required": false}
      ],
      "request_body": {         // omit the key entirely for operations with no body
        "schema": "...",
        "required": ["..."],    // documented required properties
        "optional": ["..."],    // documented optional properties
        "omit_unset_optional_fields": true
      },
      "responses": {"<status>": "<schema name>"},
      "async": {                // only on the operation that starts async work
        "returns": "RequestTracker",
        "tracker_id_field": "...",
        "poll_operation": "getRequestTracker"
      }
    }
  ],
  "schemas": {
    "RequestTracker": {
      "required": ["..."],
      "optional": ["..."],
      "enums": {"status": ["..."]},
      "terminal_states": ["..."],
      "resource_collection_field": "..."
    },
    "Machine": {
      "required": ["..."],
      "enums": {"powerState": ["..."]}
    }
  }
}
```

Property names inside `request_body`, `schemas` and `query_parameters` are the
names that go on the wire, not Python attribute names.

## `docs/official_sources.json`

Every reference page consulted to build the contract. Shape:

```jsonc
{
  "sources": [
    {
      "url": "https://developer.broadcom.com/xapis/...",
      "operation": "createMachine",   // contract operation id, or "security" / "overview"
      "title": "...",
      "fetched_on": "YYYY-MM-DD"
    }
  ]
}
```

Each of the three contract operations needs at least one source, and no two
operations may be evidenced by the same page.
