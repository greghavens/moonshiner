# vcfsupport — failed-task diagnosis for VMware Cloud Foundation 9.0

A dependency-free Python package that talks to the VMware Cloud Foundation 9.0
SDDC Manager REST API to diagnose a failed task, collect the logs and events
that explain it, and retry the task.

## Layout

| Path | Purpose |
| --- | --- |
| `docs/contract.json` | Local REST contract, derived from the SDDC Manager OpenAPI specification |
| `docs/official_sources.json` | Provenance of the contract: repository, tag, commit, spec path, operationIds |
| `tests/sddc_mock.py` | Loopback mock of SDDC Manager, pinned to `docs/contract.json` |
| `tests/fixtures.json` | The appliance state the mock serves |
| `tests/verify.py` | Deterministic verifier |
| `vcfsupport/` | The package to implement |

`docs/` and `tests/` are protected fixtures. Only `vcfsupport/` is an
implementation area.

## Contract

`docs/contract.json` names seven operations and nothing else; the mock serves
exactly those and answers `404` for anything else:

| operationId | Method | Path |
| --- | --- | --- |
| `createToken` | POST | `/v1/tokens` |
| `getTasks` | GET | `/v1/tasks` |
| `getTask` | GET | `/v1/tasks/{id}` |
| `getNotifications` | GET | `/v1/notifications` |
| `startSupportBundle` | POST | `/v1/system/support-bundles` |
| `getSupportBundleStatus` | GET | `/v1/system/support-bundles/{id}` |
| `retryTask` | PATCH | `/v1/tasks/{id}` |

Every operation except `createToken` requires
`Authorization: Bearer <TokenPair.accessToken>`.

Optional fields are optional on the wire. An optional request property or query
parameter that has no value must be left out of the request entirely — not sent
as `null`, `""`, `false`, `[]` or `{}`.

## Entry point

```python
vcfsupport.diagnose_failure(base_url: str, username: str, password: str) -> dict
```

## Report keys

| Key | Value |
| --- | --- |
| `failedTaskId` | id of the diagnosed task |
| `failedTaskName` | its `name` |
| `rootCauseErrorCode` | `errorCode` of the error on the deepest `FAILED` stage |
| `rootCauseReferenceToken` | `referenceToken` of that same error |
| `affectedComponentTypes` | sorted list of the derived component types |
| `correlatedNotificationTypes` | sorted `type` values of the correlated notifications |
| `logsRequested` | sorted `Logs` flags that were set on the support bundle request |
| `supportBundleId` | id returned by `startSupportBundle` |
| `supportBundleName` | `bundleName` of the finished collection |
| `supportBundleStatus` | terminal status of the collection |
| `retried` | `true` once `retryTask` succeeded |

## Derivation rules

* **Which task.** The `Failed` task with the latest `completionTimestamp`. The
  list view returned by `getTasks` does not carry the sub-task tree, so the
  detail has to be fetched with `getTask`.
* **Root cause.** The error attached to the deepest `FAILED` stage in that
  task's sub-task tree.
* **Correlated notifications.** A notification correlates when its `severity` is
  `ERROR` or `CRITICAL`, its `expirationTimestamp` is still in the future, and
  its `domain.id` is the id of the `DOMAIN` resource on the failed task.
* **Affected component types.** The union of the `type` values of the resources
  on every `FAILED` sub-task, at any depth, and of the resources on the
  correlated notifications. Only the four types below count; anything else
  (`DOMAIN`, `CLUSTER`, …) is ignored.

  | Resource type | `Logs` flag |
  | --- | --- |
  | `ESXI` | `esxLogs` |
  | `NSXT_MANAGER` | `nsxLogs` |
  | `VCENTER` | `vcLogs` |
  | `SDDC_MANAGER` | `sddcManagerLogs` |

* **Support bundle.** `scope.domains` holds one entry naming the failed task's
  `DOMAIN` resource and its `CLUSTER` resources; `logs` sets exactly the flags
  for the affected component types, each to `true`. Poll
  `getSupportBundleStatus` no faster than once per second until the status is
  terminal (`COMPLETED_WITH_SUCCESS` or `COMPLETED_WITH_FAILURE`).
* **Remediation.** Retry the diagnosed task only after the collection has
  finished.

## Verify

```
python3 tests/verify.py
```

The verifier starts the loopback mock, runs `diagnose_failure` against it and
asserts both the report and the exact wire shape of every request. It contacts
no live VMware endpoint.
