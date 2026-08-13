# vCenter role inventory client (VMware Cloud Foundation 9.0)

A small Java client for the vSphere Automation API that produces a stable inventory of the
authorization roles defined on a vCenter Server.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The authoritative wire contract for this client. |
| `docs/official_sources.json` | Where the contract came from: spec path, tag, commit and operation ids. |
| `src/main/java/com/example/vcf/RoleInventoryClient.java` | The client. **The only file you need to change.** |
| `src/main/java/com/example/vcf/support/Json.java` | Dependency-free JSON reader/writer, provided for you. |
| `harness/src/...` | Test harness: the loopback mock, the request-log verifier and the entry point. |
| `harness/fixtures/roles.json` | The role set the mock serves, and its paging plan. |
| `verify.sh` | Builds everything and runs the harness. |

## Running

Requires a JDK 17 or newer on the `PATH`; there are no other dependencies.

```sh
./verify.sh
```

The harness starts a mock vCenter on 127.0.0.1 with an ephemeral port, runs the client against it
four times, and checks both the report and the exact shape of every request the client sent. It
logs each request to `build/requests.jsonl`, which is useful when an assertion fails. No live
VMware endpoint is contacted.

`docs/contract.json`, `harness/` and `verify.sh` are fixed inputs; `verify.sh` refuses to run if
they have been edited.

## The contract in one paragraph

`Vcenter.Authorization.Roles_list` is `GET /api/vcenter/authorization/roles`, authenticated with a
`vmware-api-session-id` header. Its `filter` and `iterate` parameters are exploded form parameters,
so their properties travel as the top-level query keys `is_system`, `page_size` and `marker`. Any
of them that is unset must be left out of the query string entirely. The collection is paged with
an opaque marker: the first request omits `marker`, each response carries the `marker` to pass
next, and the traversal ends when a response omits `marker` or returns it as null. A page shorter
than `page_size` does *not* mean the end of the collection. Read `docs/contract.json` for the details,
including the exact report format.

## Third-party material

`docs/contract.json` is derived from the vSphere Automation OpenAPI document published by Broadcom
in [vmware/vcf-api-specs](https://github.com/vmware/vcf-api-specs) under the Apache-2.0 licence.
See `docs/official_sources.json` for the pinned revision.
