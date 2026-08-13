# SDDC LCM task inventory collector

A standard-library-only Python package that collects the **complete** VMware Cloud
Foundation 9.1 SDDC LCM task inventory across every page of a paginated collection
and emits it in a stable, deterministic order.

Nothing here may import a third-party package. `python3` 3.9 or later, stdlib only.

---

## 1. Derive the contract from the specification

The wire contract must come from the published OpenAPI document, **not** from a
documentation or reference web page.

* Repository: `vmware/vcf-api-specs` on GitHub (SPDX licence `Apache-2.0`)
* Spec path: `specifications/sddc-lcm/sddc-lcm-openapi.yaml`
* Commit: `c3f3b52c845dd967cabbc21680e893292077d5ba`

Fetch that file and derive the contract for exactly two operations, named by their
specification `operationId`:

| operationId | what it is |
|---|---|
| `getTasks` | the paginated task collection |
| `getTask`  | a single task by id |

Serve nothing else and name nothing else.

### 1.1 `docs/contract.json`

Write the derived contract to `docs/contract.json`. The loopback mock used during
verification builds its routes, its parameter validation and its auth check from
this file, so the structure below is exact. Unknown extra top-level keys are
ignored; missing or misspelled ones fail.

```json
{
  "server": { "url": "<the servers[0].url from the spec>" },
  "securitySchemes": {
    "bearerToken": { "type": "...", "scheme": "...", "bearerFormat": "..." }
  },
  "operations": {
    "getTasks": {
      "method": "GET",
      "path": "/v1/tasks",
      "security": ["bearerToken"],
      "parameters": [
        { "name": "<wire name>", "in": "query", "required": false, "type": "string" }
      ],
      "requestBody": null,
      "responses": { "200": "PageOfTaskSummary", "500": "..." }
    },
    "getTask": { "...": "same shape" }
  },
  "schemas": {
    "TaskStatus":  { "enum": ["..."] },
    "PageMetadata": { "properties": ["..."] },
    "TaskSummary": { "required": ["..."], "properties": ["..."] }
  }
}
```

Rules for the fields above:

* `method` is upper-case. `path` is the spec's path template verbatim, including
  `{taskId}` braces where present.
* `security` lists the security scheme names that apply to the operation after
  the document-wide requirement and any operation-level override are taken into
  account.
* `parameters` lists **every** parameter the spec declares for the operation, in
  the order the spec declares them, with the spec's own `name`, its `in`
  (`query` or `path`), its `required` flag and its schema `type`.
  * A parameter whose schema carries a `format` records it as `"format": "<value>"`.
  * A parameter whose schema carries a `default` records it as `"default": <value>`.
  * `getTasks` documents a ceiling on `pageSize` in that parameter's prose
    `description` rather than in its schema. Record it as `"maximum": <n>` on
    that parameter.
* `requestBody` is `null` for an operation that takes no body.
* `responses` maps each documented status code, as a string, to the **name** of
  the schema that code returns. One of the two operations returns something other
  than `ErrorResponse` on `"500"`; the specification is the only place that says
  so, and the contract must say what the spec says.
* `schemas.TaskStatus.enum` is the enum in spec order.
* `schemas.PageMetadata.properties` and `schemas.TaskSummary.properties` are lists
  of property names in spec order. `schemas.TaskSummary.required` is the spec's
  required list.

### 1.2 `docs/official_sources.json`

Record provenance for what was actually fetched:

```json
{
  "repository": "https://github.com/vmware/vcf-api-specs",
  "license": "Apache-2.0",
  "spec_path": "specifications/sddc-lcm/sddc-lcm-openapi.yaml",
  "commit_sha": "<the 40-hex commit above>",
  "blob_sha": "<git blob sha of the file at that commit>",
  "sha256": "<SHA-256 over the exact bytes fetched>",
  "operation_ids": ["getTasks", "getTask"]
}
```

`blob_sha` is the git object id GitHub reports for that path at that commit.
`sha256` is over the raw bytes, not over a re-serialisation.

---

## 2. Implement the package

### 2.1 `src/vcf_sddc_lcm/contract.py`

```python
class Contract:
    @classmethod
    def load(cls, path) -> "Contract": ...

    def operation(self, operation_id) -> dict              # KeyError if not named
    def query_parameters(self, operation_id) -> list       # wire names, spec order
    def build_target(self, operation_id, path_params=None, query=None) -> str
```

`build_target` returns the request target: the operation's path with
`{placeholders}` substituted, plus a `?`-joined query string when `query` is
non-empty and no `?` at all when it is empty. Every request target the client
issues must come from `build_target`, i.e. from the contract, never from a path
literal in the client.

### 2.2 `src/vcf_sddc_lcm/client.py`

```python
class SddcLcmClient:
    def __init__(self, base_url: str, token: str, contract: Contract, timeout: float = 10.0): ...

    def get_task(self, task_id) -> dict
    def list_tasks(self, filters=None, page_size=None) -> list          # summaries
    def collect_tasks(self, filters=None, page_size=None) -> dict
```

`filters` is keyed by the **wire** parameter names exactly as the specification
spells them (`createdBy`, `startTimeGt`, `includeSystemTasks`, ...). A key that is
not a declared query parameter of `getTasks`, or that is one of the two paging
parameters (`pageNumber`, `pageSize`), raises `ValueError` — paging belongs to the
client, not the caller's filter dict. Values are sent as supplied; date-time
filters are passed through verbatim as strings.

`page_size` is the caller's page size. When it is `None`, fall back to the
`maximum` the contract records for the `pageSize` parameter — the ceiling the
specification documents in that parameter's prose. Both paging parameters are sent
on every `getTasks` request.

`list_tasks` returns every element of the collection, from every page, in stable
order. `collect_tasks` returns:

```json
{ "tasks": [ "<every summary, stable order>" ],
  "failed_task_details": [ "<full Task for each FAILED summary, same relative order>" ] }
```

`failed_task_details` is produced by calling `getTask` once per summary whose
`status` is `FAILED`, walking the already-ordered `tasks` list in order.

### 2.3 Stable order

Sort the collected summaries by:

1. `startTime` ascending, compared as the ISO 8601 string, for every summary that
   has a `startTime`;
2. `id` ascending, as a tie-break;
3. every summary with **no** `startTime` sorts after every summary that has one,
   ordered among themselves by `id` ascending.

`startTime` is optional in `TaskSummary` — the spec marks only `id` as required —
so case 3 is reachable and must not raise.

---

## 3. Required wire behaviour

These are asserted from the mock's request log, byte for byte.

1. **Authentication.** Every request to a bearer-protected operation carries
   `Authorization: Bearer <token>`.
2. **Paging is complete and minimal.** Start at `pageNumber` 0 and continue until
   every page has been retrieved, using `pageMetadata.totalPages` to know when to
   stop. Each page is requested exactly once, in ascending order, and no page
   beyond the last is requested. `totalElements` is not a page count.
3. **`pageNumber` 0 is sent.** Page zero is a real value, not a missing one. A
   value of `0` — like a value of `false` — is set, and set values are sent.
4. **Unset optional fields are omitted.** Any optional query parameter the caller
   did not supply is absent from the query string entirely. Not `status=`, not
   `status=null`, not `status=None`.
5. **A declared `default` is not a value.** `includeSystemTasks` declares a schema
   default in the spec. That default describes what the *server* does when the
   parameter is absent; it does not license the client to send it. When the caller
   omits `includeSystemTasks` it must not appear in the query string at all.
6. **Booleans serialise as JSON booleans.** When the caller *does* supply
   `includeSystemTasks`, it goes on the wire as `true` or `false` — lower-case,
   never `True`, `False`, `1` or `0`.
7. **Values are percent-encoded.** A date-time such as
   `2026-05-01T00:00:00+00:00` must survive the round trip. In a query string a
   raw `+` means a space, so it has to be encoded (`%2B`), as does `:` (`%3A`).
   The query string the client sends contains no bare `+`.
8. **`getTasks` and `getTask` are GETs with no request body**, and no
   `Content-Type` header.
9. **Nothing outside the contract is requested.** The mock serves only the two
   operations named in `docs/contract.json` and logs everything else as a miss.
10. **Targets come from the contract.** `client.py` contains no `/v1/...` path
    literal; every target is produced by `Contract.build_target`.

---

## 4. Verification

```
python3 -B .protected/verify.py
```

It starts a loopback HTTP mock pinned to your `docs/contract.json`, drives the
client against it, and asserts the contract, the provenance and the request log.
Everything under `.protected/` is protected — read it and run it, but do not
modify it. No VMware endpoint is contacted during verification.
