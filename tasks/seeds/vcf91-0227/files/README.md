# vcf-lcm-upgrade

A stdlib-only Python client for the **VCF 9.1 SDDC LCM Service** component
upgrade workflow, driven by a contract derived from the published OpenAPI
specification.

Applying an upgrade to an SDDC/Fleet component is **asynchronous**: the service
accepts the request and returns a `Task`, and the upgrade is not done until that
task reaches a terminal state. Upgrades also run long enough that the access
token presented at the start of the run **can expire before the task finishes**.
When that happens the run must obtain a fresh token and carry on from where it
was — it must not restart the upgrade.

Everything here is standard library only — no `requests`, no `pyyaml`, no
`pytest`, no third-party HTTP or schema libraries.

---

## 1. Derive the contract from the specification

The contract must come from the **OpenAPI specification file**, not from a
documentation or reference web page.

* Repository: `vmware/vcf-api-specs` on GitHub (Apache-2.0)
* Specification file: `specifications/sddc-lcm/sddc-lcm-openapi.yaml`
* Repository commit: `c3f3b52c845dd967cabbc21680e893292077d5ba`

Retrieve that file at the pinned commit and derive every value below from it.
Five operations are in scope, named here by their exact `operationId`:

| operationId              | role in the workflow                                          |
| ------------------------ | ------------------------------------------------------------- |
| `getHealth`              | pre-flight check before any credential is used                |
| `resolveDepotComponents` | resolves component versions to depot binary URLs              |
| `performComponentAction` | starts the `apply` action on a component, returning a `Task`  |
| `getTask`                | polls that task until it is terminal                          |
| `retryTask`              | resumes a failed task from the stage that failed              |

### 1.1 `docs/contract.json`

Write the derived contract to `docs/contract.json` with exactly this shape.
Every value is read off the specification; nothing here is invented.

```jsonc
{
  "source": {
    "repository": "<https URL of the GitHub repository>",
    "commit": "<40-hex sha of the repository revision you retrieved it from>",
    "spec_path": "specifications/sddc-lcm/sddc-lcm-openapi.yaml",
    "openapi": "<the spec's top-level openapi version>",
    "info_version": "<info.version>",
    "server_url": "<the single entry in servers[].url>"
  },
  "security": {
    "scheme_name": "<the key under components.securitySchemes referenced by the
                     top-level security requirement>",
    "type": "<that scheme's type>",
    "scheme": "<that scheme's scheme>",
    "bearer_format": "<that scheme's bearerFormat>"
  },
  "operations": {
    "<operationId>": {
      "method": "<uppercase HTTP method>",
      "spec_path_key": "<the key under paths: exactly as it appears there>",
      "path": "<spec_path_key with any embedded query string removed>",
      "authenticated": <false only where the operation overrides the top-level
                        security requirement with an empty one>,
      "success_status": <the single 2xx status code declared for it, as an int>,
      "response_schema": "<name of the schema the 2xx response resolves to>",
      "path_parameters": [
        {"name": "...", "required": true, "type": "...", "format": "..."}
      ],
      "query_parameters": [
        {"name": "...", "required": true, "type": "...",
         "enum": ["..."] | null,
         "fixed_value": "..." | null,
         "source": "parameters" | "path_key"}
      ],
      "header_parameters": [
        {"name": "...", "required": false, "type": "..."}
      ],
      "request_body": null | {
        "schema": "<request body schema name>",
        "content_type": "application/json"
      }
    }
  },
  "schemas": {
    "<SchemaName>": {
      "type": "object",
      "required": ["<properties listed under the schema's required:>"],
      "properties": [
        {"name": "...", "type": "...", "format": "..." | null,
         "schema": "<referenced schema name>" | null,
         "items": {"type": "...", "schema": "..." | null} | null}
      ]
    }
  },
  "task_status": {
    "all": ["<every member of the TaskStatus enum, in specification order>"],
    "terminal": ["<the members that represent a finished outcome>"],
    "successful": ["<the member that represents a successful outcome>"]
  }
}
```

Notes on the fields that need a judgement call:

* **`spec_path_key` vs `path`.** Most operations are keyed under `paths:` by a
  plain path template, and the two are identical. At least one operation in
  scope is keyed by a template that already carries a query string; for that
  one, `path` is the part before the `?` and the query it carried shows up in
  `query_parameters`.
* **`query_parameters`.** `source` is `"parameters"` for a query parameter
  declared in the operation's `parameters:` list, and `"path_key"` for one that
  only exists because it is baked into the `paths:` key. A `"path_key"` query
  parameter has a `fixed_value` (the literal value from the key) and a `null`
  `enum`; a `"parameters"` one has a `null` `fixed_value` and carries the
  `enum` the spec declares, or `null` when it declares none.
* **`authenticated`.** The spec applies one security requirement to the whole
  document. An operation is `false` here only when it overrides that with an
  empty requirement of its own.
* `format` contains the format the spec declares, or `null` when the parameter
  or property has no declared format.
* `header_parameters` lists only header parameters actually declared on that
  operation. An operation with none gets `[]`. Same for `path_parameters` and
  `query_parameters`.
* `request_body` is `null` for operations that declare no request body.
* **`schemas`** must contain exactly the request-body schemas of the operations
  in scope plus every schema reachable from them by `$ref`, and nothing else.
  `properties` lists them in the order the spec declares them. For a property
  that is a bare `$ref`, `type` is the referenced schema's `type` and `schema`
  is its name. For an array property, `items` describes the element type
  (`schema` being the referenced schema name, or `null` for a primitive
  element). Defaults and examples are not recorded.
* `required` is `[]` when a schema declares no `required:` list, and otherwise
  lists the entries in the order the spec declares them.
* In `task_status`, **terminal** means the statuses from which the task makes no
  further transition — the finished outcomes (success, failure, cancellation).
  The remaining members are the in-flight statuses. **successful** is the subset
  of terminal statuses that indicate the work completed successfully.

### 1.2 `docs/official_sources.json`

Record provenance for the specification you actually retrieved:

```jsonc
{
  "sources": [
    {
      "repository": "<https URL of the GitHub repository>",
      "license": "<the repository's SPDX license id>",
      "spec_path": "specifications/sddc-lcm/sddc-lcm-openapi.yaml",
      "commit": "<same 40-hex commit sha as contract.json>",
      "spec_blob_sha": "<the git blob sha of the spec file>",
      "spec_sha256": "<sha256 of the spec file's bytes>",
      "operation_ids": ["<each operationId used, exactly as spelled in the spec>"]
    }
  ]
}
```

`spec_blob_sha` is what `git hash-object` reports for the file (GitHub's
contents API reports the same value as `sha`). `spec_sha256` is the SHA-256 of
the file's raw bytes. Both must describe the file at the recorded commit — they
are how the retrieval is checked, so compute them from the bytes you actually
fetched.

---

## 2. Implement the client

Fill in `src/vcf_lcm/contract.py` and `src/vcf_lcm/client.py`. The stubs define
the API surface; do not rename or re-signature the public entry points.
`src/vcf_lcm/errors.py` is already complete — use those exception types.

`SddcLcmClient` **builds its request targets from the loaded contract**: the
method, path template and query parameters for each operation come from
`docs/contract.json`, not from string literals in the client.

### 2.1 Token handling

The client is constructed with a `token_provider` — a zero-argument callable
returning the current access token. It is not a constant, and it is not free to
call; the run is judged on when and how often it is called.

1. **Lazy.** `token_provider` is not called during construction, and not for an
   operation the contract marks `"authenticated": false`. It is first called
   immediately before the first authenticated request. The value is cached and
   reused for subsequent requests.

2. **Refresh on 401, then replay once.** When an authenticated request comes
   back `401`, the client calls `token_provider` again, replaces the cached
   token, and re-sends **that same request** — same method, same target, same
   body bytes — exactly once, with the new token. A `401` on the replay raises
   `TokenRefreshError`. A `401` from an unauthenticated operation is not
   refreshable: it raises `LcmApiError` without obtaining a token. No other
   status triggers a refresh.

3. **No work is lost.** The refresh happens inside the single request that was
   rejected. Nothing earlier in the workflow is re-issued: an upgrade started
   before the token expired is never started a second time, and polling resumes
   on the same task id.

### 2.2 Required wire behaviour

1. **Authentication.** Every request for an operation the contract marks
   `"authenticated": true` carries `Authorization: Bearer <token>`, matching the
   security scheme in the contract. `getHealth` is marked `false` and carries no
   `Authorization` header at all.

2. **Optional fields are omitted, never sent empty.** A request body is built
   only from the optional properties the caller actually supplied. An optional
   object that the caller did not populate is absent from the body — not present
   as `null` and not present as `{}`. A supplied value is sent even when it is
   falsey (`false` and `""` are real values); only `None` means "unset".

3. **Optional headers are omitted, never sent empty.** When no correlation id is
   supplied, the `X-Correlation-Id` header is absent from the request entirely —
   not present with an empty value.

4. **Requests with no body send no body.** A request for an operation whose
   contract entry has `"request_body": null` carries no payload and no
   `Content-Type` header — including the `POST` operations that declare none.

5. **Query parameters come from the contract.** An operation whose query
   parameter declares a `fixed_value` always sends exactly that value; one that
   declares an `enum` only ever sends a member of it. Operations with no query
   parameters send no query string.

6. **Only the five contracted operations are ever called.** No probing of other
   paths, no unversioned health checks, no trailing-slash variants.

7. **The task is polled to a terminal state.** Completion is never assumed from
   the `202` response. `await_task` performs its first task read immediately,
   then re-reads the task until its `status` is one of the contract's terminal
   statuses, sleeping `poll_interval` seconds between reads. The timeout is
   evaluated after each non-terminal read, and polling stops as soon as a
   terminal status is observed.

8. **Terminal outcomes are distinguished.** A terminal status in
   `task_status.successful` returns the task; any other terminal status raises
   `TaskFailedError` carrying that task. Exceeding `timeout` raises
   `TaskTimeoutError`.

9. **HTTP errors.** A non-2xx response that is not a recoverable `401` raises
   `LcmApiError` with `status_code` set and the decoded `ErrorResponse` body in
   `payload` when the body is JSON.

### 2.3 The `resolveDepotComponents` body

`components` is a sequence of `(component, version)` pairs. A pair whose version
is `None` asks the depot to resolve the latest, and must serialise to an object
carrying only the component name — the spec makes that property optional, so it
is omitted, not sent as `null` or `""`.

### 2.4 The `performComponentAction` body

`correlation_id`, when supplied, sets **both** the optional `X-Correlation-Id`
header and the body's optional correlation property. When it is `None`, neither
appears. `perform_backup` is the only property of the platform spec object and
that object is required to carry it, so when `perform_backup` is `None` the
whole platform spec object is omitted from the body.

---

## 3. Local mock service

`.protected/lcm_mock_server.py` is a loopback-only HTTP service pinned to your
contract: it reads `docs/contract.json`, builds its routing table and its
request-body validation from the operations and schemas named there, and serves
**nothing else**. If the contract names an operation it does not implement, or
omits a required one, it refuses to start.

It models an expiring access token: after a configurable number of successful
authenticated requests it rotates its token, and every later request presenting
the old one gets a `401` until the new token is used.

Run it by hand while developing:

```sh
python3 .protected/lcm_mock_server.py --contract docs/contract.json \
    --log /tmp/req.jsonl --expire-after 3
```

It prints one line, `READY <base-url> <initial-token> <rotated-token>`, then
serves on `127.0.0.1` on an ephemeral port. Every request is appended to the log
file as one JSON object per line, including the full header list and the raw
body bytes, so the exact wire shape is inspectable.

The mock contacts no VMware endpoint, and neither does the test suite.

---

## 4. Verify

```sh
python3 -B .protected/verify.py
```

Everything under `.protected/` is protected: read it, run it, but do not modify
it. Create `docs/contract.json` and `docs/official_sources.json`, and fill in
`src/vcf_lcm/contract.py` and `src/vcf_lcm/client.py`.
