# vcf-lcm-support-bundle

A stdlib-only Python client for the **VCF 9.1 SDDC LCM Service** support-bundle
workflow, driven by a contract derived from the published OpenAPI specification.

Generating a support bundle on a Fleet/SDDC LCM component is an **asynchronous**
operation: the service accepts the request and returns a `Task`. The bundle does
not exist until that task reaches a terminal state. This package must poll the
task to a terminal state and only then look up the produced bundle.

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
Three operations are in scope, named here by their exact `operationId`:

| operationId                     | role in the workflow                              |
| ------------------------------- | ------------------------------------------------- |
| `generateComponentSupportBundle`| starts the asynchronous bundle generation         |
| `getTask`                       | polls the returned task until it is terminal      |
| `getComponentSupportBundles`    | lists the bundles once the task has succeeded     |

### 1.1 `docs/contract.json`

Write the derived contract to `docs/contract.json` with exactly this shape. Every
value is read off the specification; nothing here is invented.

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
      "path": "<path template exactly as it appears under paths:>",
      "success_status": <the single 2xx status code declared for it, as an int>,
      "response_schema": "<name of the schema the 2xx response resolves to>",
      "response_is_array": <true if the 2xx response is an array of that schema>,
      "path_parameters": [
        {"name": "...", "required": true, "type": "...", "format": "..."}
      ],
      "header_parameters": [
        {"name": "...", "required": false, "type": "..."}
      ],
      "request_body": null | {
        "schema": "<request body schema name>",
        "content_type": "application/json",
        "required_properties": ["<properties listed under the schema's required:>"],
        "optional_properties": [{"name": "...", "type": "..."}]
      }
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

* `format` contains the format the spec declares, or `null` when the parameter
  has no declared format.
* `header_parameters` lists only header parameters actually declared on that
  operation. An operation with none gets `[]`.
* `request_body` is `null` for operations that declare no request body.
* `required_properties` is `[]` when the body schema declares no `required:` list.
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

`spec_blob_sha` is what `git hash-object` reports for the file (GitHub's contents
API reports the same value as `sha`). `spec_sha256` is the SHA-256 of the file's
raw bytes. Both must describe the file at the recorded commit — they are how the
retrieval is checked, so compute them from the bytes you actually fetched.

---

## 2. Implement the client

Fill in `src/vcf_lcm/contract.py` and `src/vcf_lcm/client.py`. The stubs define
the API surface; do not rename or re-signature the public entry points.

`SddcLcmClient` **builds its request URLs from the loaded contract** — the method
and path template for each operation come from `docs/contract.json`, not from
string literals in the client.

### Required wire behaviour

1. **Authentication.** Every request carries
   `Authorization: Bearer <token>`, matching the security scheme in the contract.

2. **Optional fields are omitted, never sent empty.** The request body for
   `generateComponentSupportBundle` is a JSON object built only from the optional
   properties the caller actually supplied. When the caller supplies none, the
   body serialises to exactly `{}` — not `{"lookBackWindow": null}`, not
   `{"lookBackWindow": 0}`, and not an empty payload. A supplied value of `0` is
   a real value and **must** be sent; only `None` means "unset".

3. **Optional headers are omitted, never sent empty.** When no correlation id is
   supplied, the `X-Correlation-Id` header is absent from the request entirely —
   not present with an empty value.

4. **`GET` requests carry no body and no `Content-Type` header.**

5. **Only the three contracted operations are ever called.** No probing of other
   paths, no unversioned health checks, no trailing-slash variants.

6. **The task is polled to a terminal state.** Completion is never assumed from
   the `202` response. `await_task` performs its first task read immediately,
   then re-reads the task until its `status` is one of the contract's terminal
   statuses, sleeping `poll_interval` seconds between reads. The timeout is
   evaluated after each non-terminal read, and polling stops as soon as a
   terminal status is observed.

7. **Terminal outcomes are distinguished.** A terminal status in
   `task_status.successful` returns the task; any other terminal status raises
   `TaskFailedError` carrying that task. Exceeding `timeout` raises
   `TaskTimeoutError`.

8. **Ordering.** `generate_support_bundle_and_wait` starts the task, polls it to
   a terminal state, and only afterwards lists the component's support bundles,
   returning the bundle whose `id` equals the terminal task's `resourceId`. If no
   such bundle is present, raise `LcmApiError`.

9. **HTTP errors.** A non-2xx response raises `LcmApiError` with `status_code`
   set and the decoded `ErrorResponse` body in `payload` when the body is JSON.

---

## 3. Local mock service

`.protected/lcm_mock_server.py` is a loopback-only HTTP service pinned to your
contract: it reads `docs/contract.json`, builds its routing table from the
operations named there, and serves **nothing else**. If the contract names an
operation it does not implement, or omits a required one, it refuses to start.

Run it by hand while developing:

```sh
python3 .protected/lcm_mock_server.py --contract docs/contract.json --log /tmp/req.jsonl
```

It prints one line, `READY <base-url> <token>`, then serves on `127.0.0.1` on an
ephemeral port. Every request is appended to the log file as one JSON object per
line, including the full header list, so the exact wire shape is inspectable.

The mock contacts no VMware endpoint, and neither does the test suite.

---

## 4. Verify

```sh
python3 -B .protected/verify.py
```

Everything under `.protected/` is protected: read it, run it, but do not modify
it. Create `docs/contract.json` and `docs/official_sources.json`, and fill in
`src/vcf_lcm/contract.py` and `src/vcf_lcm/client.py`.
