# VcfEvcGuard

A small PowerShell module that changes the Enhanced vMotion Compatibility (EVC)
mode of a vSphere cluster in VMware Cloud Foundation 9.0 — but only after the
vCenter-side precheck says the change is safe.

EVC mode gained a first-class REST surface in vSphere API 9.0.0.0. The
`Vcenter.Cluster.EvcMode` resource exposes a check operation and a set
operation over the same path; the check is the whole point, because pushing an
EVC baseline a host cannot satisfy is how a cluster ends up with hosts it can no
longer admit. This module runs the check, reads the findings, and applies the
change only when there are none.

The `VMware.Sdk.Vcf.*` PowerCLI modules are installed in this environment as a
prerequisite. Treat them as a dependency you declare and import — do not copy
any part of them into this repository.

---

## 1. `docs/contract.json`

The wire contract this module is pinned to, derived from
`specifications/vsphere/openapi/automation/vcenter.yaml` at tag **9.0.0.0** of
the `vmware/vcf-api-specs` repository (Apache-2.0). Read the specification
file itself. The same file exists at tag 9.1.0.0 and describes a later
contract; that one is wrong for this module.

```jsonc
{
  "source": {
    "repository": "vmware/vcf-api-specs",
    "path":       "specifications/vsphere/openapi/automation/vcenter.yaml",
    "tag":        "<the tag this is derived from>",
    "commit":     "<the full 40-character commit sha that tag points at>",
    "license":    "Apache-2.0",
    "openapi":    "<the spec's openapi version>",
    "info_version": "<the spec's info.version>",
    "server_url": "<the single entry in the spec's servers block, verbatim>"
  },

  "security_schemes": {
    // Only the two schemes the five operations below actually use.
    // Copy each scheme's fields from the spec's components.securitySchemes:
    // an http scheme records "type" and "scheme"; an apiKey scheme records
    // "type", "name" and "in".
    "<scheme name>": { ... }
  },

  "operations": {
    // Exactly these five keys, no others.
    "createSession":   { ... },
    "getEvcMode":      { ... },
    "checkSetEvcMode": { ... },
    "setEvcMode":      { ... },
    "getTask":         { ... }
  },

  "schemas": {
    // Required vs optional properties for each schema the operations exchange.
    "<schema name>": { "required": ["..."], "optional": ["..."] }
  },

  "task_status_values": [ /* the Cis.Task.Status enumeration */ ]
}
```

Each entry under `operations` has exactly these fields:

| field                  | meaning                                                                                     |
| ---------------------- | ------------------------------------------------------------------------------------------- |
| `operationId`          | the `operationId` string the spec gives the operation, verbatim                             |
| `method`               | HTTP method, upper case                                                                     |
| `path`                 | the spec's path template with any query string stripped, e.g. `/cis/tasks/{task}`           |
| `path_parameters`      | names of the `in: path` parameters, as a list                                               |
| `query`                | the fixed query parameters baked into the spec's path key, as a string→string object; `{}` if none |
| `optional_query_schema`| the schema name of an `in: query` parameter the caller may supply, else `null`               |
| `security`             | the security scheme names the operation lists                                               |
| `request_body`         | the schema name of the request body, or `null` when the operation takes none                |
| `request_body_required`| the body's `required` flag; `false` when there is no body                                   |
| `success_status`       | the non-error response status, as an integer                                                |
| `response_schema`      | the schema name of the success response; use the string `"string"` for a bare string        |
| `error_statuses`       | every error status the operation documents, as a list of integers                           |

The five operations to cover are: creating a session, reading a cluster's EVC
mode, checking a proposed EVC mode, setting a cluster's EVC mode, and reading a
task. `schemas` must cover every schema named by `request_body`, plus
`Vcenter.Cluster.EvcMode.CheckResult` and the schema behind
`optional_query_schema`.

## 2. `docs/official_sources.json`

```jsonc
{
  "sources": [
    {
      "repository":    "vmware/vcf-api-specs",
      "path":          "<path of the spec file within the repo>",
      "tag":           "<tag>",
      "commit":        "<full commit sha>",
      "url":           "<absolute https URL, pinned to the tag or the commit — not to a branch>",
      "license":       "Apache-2.0",
      "operation_ids": [ /* the operationIds this source is the authority for */ ],
      "retrieved":     "YYYY-MM-DD"
    }
  ]
}
```

Between them the sources must account for all five `operationId`s.

## 3. `VcfEvcGuard/VcfEvcGuard.psd1`

Write the module manifest. It must:

- set `RootModule` to `VcfEvcGuard.psm1`,
- export exactly `Connect-VcfEvcGuardServer` and `Invoke-VcfEvcModeGuardedSet`,
- declare a `VMware.Sdk.Vcf.*` module in `RequiredModules`, so importing
  `VcfEvcGuard` pulls the PowerCLI SDK in with it,
- pass `Test-ModuleManifest`.

Use the SDK for the plumbing it already provides rather than reimplementing it —
`[VMware.Binding.OpenApi.Client.ClientUtils]` has `Base64Encode` and `UrlEncode`,
and `Connect-VcfSddcManagerServer` is the shape `Connect-VcfEvcGuardServer`
mirrors.

## 4. `VcfEvcGuard/VcfEvcGuard.psm1`

The stub declares both parameter blocks; keep them as they are and fill in the
bodies.

### `Connect-VcfEvcGuardServer`

Builds the service URI as `{Protocol}://{Server}:{Port}` followed by the base
path from the spec's `servers` block, then performs the session-creation
operation against it. Returns a `[pscustomobject]` with:

| property        | value                                                    |
| --------------- | -------------------------------------------------------- |
| `ServiceUri`    | the API base URI, no trailing slash                      |
| `User`          | the credential's user name                               |
| `SessionSecret` | the session token the API handed back                    |
| `IsConnected`   | `$true`                                                  |

The login call authenticates with the `basic_auth` scheme and sends no request
body. `-IgnoreInvalidCertificate` skips certificate validation for every request
made through the connection.

### `Invoke-VcfEvcModeGuardedSet`

`-EvcMode` is a hashtable shaped like `Vcenter.EvcMode.EvcMode`:

```powershell
@{ key = 'intel-skylake'; masks = @(
     @{ key = 'cpuid.AVX512F'; name = 'cpuid.AVX512F'; value = 'Val:0x00000001' }) }
```

Omitting `-EvcMode` asks vCenter to clear the cluster's EVC mode.

The call sequence, in order:

1. Read the cluster's current EVC mode.
2. Start the check for the requested spec. It is a task operation: the response
   body is the task id.
3. Poll the task until it reaches a terminal status. This task's *result* is
   what the gate reads, so ask for it — send no `Cis.Tasks.GetSpec` query
   parameters at all.
4. **The gate.** Apply the change only if the check task ended `SUCCEEDED` *and*
   carried no result. A result is a list of `Vcenter.Cluster.EvcMode.CheckResult`
   — every entry is a blocking error. If the task ended `SUCCEEDED` with a
   non-empty result, or ended `FAILED`, stop here and issue no further calls.
5. Otherwise set the EVC mode, sending the same spec that was checked.
6. Poll the set task until terminal. Nothing reads this task's result, so ask
   for it to be excluded — and set nothing else.
7. Read the cluster's EVC mode back.

Return a `[pscustomobject]`:

| property        | value                                                                                |
| --------------- | ------------------------------------------------------------------------------------ |
| `Cluster`       | the cluster identifier                                                               |
| `Applied`       | `$true` only when the set task ran and reached `SUCCEEDED`                           |
| `CheckTask`     | the check task's id                                                                  |
| `CheckStatus`   | the check task's terminal status                                                     |
| `CheckResults`  | the blocking findings, as returned; an empty array when the precheck was clean       |
| `SetTask`       | the set task's id, or `$null` when the gate blocked the change                       |
| `SetStatus`     | the set task's terminal status, or `$null`                                           |
| `EvcModeBefore` | the `evc_mode` property read in step 1, or `$null` when the cluster had none         |
| `EvcModeAfter`  | the `evc_mode` property read in step 7, or `$null`; `$null` when the gate blocked    |
| `BlockedReason` | `'PRECHECK_ERRORS'`, `'PRECHECK_TASK_FAILED'`, or `$null` when nothing blocked        |

Blocking is a normal outcome, not an error — return the object, do not throw.

`-PollIntervalSeconds` is how long to wait between polls; `-TimeoutSeconds`
bounds the wait for a single task, and exceeding it is an error.

### Two wire rules

Both follow from the same principle: a property or parameter the caller did not
set is **absent from the request**, never present-and-empty.

- **Bodies.** `Vcenter.Cluster.EvcMode.SetSpec` has one optional property. When
  it is not set the body is `{}` — not `{"evc_mode": null}`, not `{"evc_mode": {}}`.
  No `null` may appear anywhere in a request body.
- **Query parameters.** `Cis.Tasks.GetSpec` has two optional properties and the
  spec serialises it `style: form, explode: true`, so its properties travel as
  ordinary top-level query parameters. Send only the ones you set; the other
  must not appear at all, not even as an empty value.

Nothing outside the five contracted operations may be called.

---

## Running the checks

```
./verify.sh
```

It confirms the protected files are untouched, then runs the acceptance suite.
The suite starts a loopback fixture of the vCenter automation API that serves
only the five contracted operations and records every request it receives, and
drives your module against it. No VMware endpoint is contacted.

`mock/`, `tests/`, `verify.sh` and `PROTECTED.sha256` are protected and must not
be modified.
