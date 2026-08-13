# VcfLcmClient — entry point and report format

## Entry point

`TestMain` calls exactly one method on the client:

```java
public static String run(String baseUrl, String bearerToken, String requestJson) throws Exception
```

* `baseUrl` — the SDDC LCM service root including the base path from the specification's
  `servers[0].url`, for example `http://127.0.0.1:41573/sddc-lcm`. Operation paths from the
  contract are appended to it, so `getTask` resolves to
  `http://127.0.0.1:41573/sddc-lcm/v1/tasks/{taskId}`.
* `bearerToken` — the raw token for the `bearerToken` security scheme.
* `requestJson` — the contents of `fixtures/upgrade-request.json`.
* returns — the rollout report as a JSON document (see below).

`harness/Json.java` is on the classpath and may be used for parsing and serialising.
`Json.obj()` returns an insertion-ordered map, and `Json.write` emits only the keys the
map actually contains.

## Change request

The change request the client is handed has this shape:

| field | meaning |
| --- | --- |
| `changeRequest` | operator's ticket reference, informational |
| `componentType` | the fleet component to upgrade |
| `targetVersion` | the version to upgrade that component to |
| `performBackup` | whether the apply should take a pre-upgrade backup |
| `depot.fqdn`, `depot.certificate` | the fleet depot to register |
| `additionalComponents[]` | further components to resolve in the same depot pass; each entry has `component` and an optional `version` |

## Report format

```json
{
  "changeRequest": "<changeRequest from the request>",
  "componentType": "<componentType from the request>",
  "componentId": "<id discovered via getComponents>",
  "currentVersion": "<version the component reports today>",
  "targetVersion": "<targetVersion from the request>",
  "correlationId": "<the correlation id this run used>",
  "resolvedComponentVersions": [
    { "component": "...", "version": "...", "binaryUrl": "..." }
  ],
  "outcome": "SUCCEEDED | FAILED",
  "steps": [ ... ],
  "failure": { ... }
}
```

`resolvedComponentVersions` is the `componentVersions` array returned by
`resolveDepotComponents`, in the order the service returned it, each entry carrying
`component`, `version` and `binaryUrl`.

`correlationId` is a UUID the client generates once per run and reuses for the whole
rollout.

### `steps`

One entry per contract operation the rollout performs, in the order performed. Polling a
task with `getTask` is part of the step that created the task and does not get its own
entry.

| key | when present | value |
| --- | --- | --- |
| `operationId` | always | the contract `operationId` |
| `action` | only for operations that take an `action` parameter | the value sent |
| `status` | always | `SUCCEEDED` or `FAILED` |
| `taskId` | only for steps that created a task | the task id returned |

Keys that do not apply to a step are **absent** from that step's object, not present with
a null value.

A step whose operation returned a task is `SUCCEEDED` only once that task has been polled
to a terminal `SUCCEEDED` status; it is `FAILED` if the task reaches `FAILED` or
`CANCELED`. A step whose operation returns data directly is `SUCCEEDED` on a 2xx response.

### `outcome` and `failure`

`outcome` is `SUCCEEDED` when every step succeeded, otherwise `FAILED`.

`failure` is **absent** when `outcome` is `SUCCEEDED`. When a step fails, the rollout stops
there — no later operation is attempted — and `failure` describes it:

| key | value |
| --- | --- |
| `stepIndex` | zero-based index of the failing entry in `steps` |
| `operationId` | the failing step's `operationId` |
| `action` | the failing step's `action`, absent if it had none |
| `taskId` | the failing task's id |
| `taskStatus` | the terminal status of that task |
| `failedStageId` | `id` of the entry in the task's `stages` whose `status` is `FAILED` |
| `message` | `message.defaultMessage` of the first `ERROR` level entry in that stage's `messages` |

Stages that did not fail, and messages below `ERROR` level, are not reported here.
