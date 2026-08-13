# VCF Operations identity-sync triage

`vcfops_triage` is a standard-library-only Python package that diagnoses one
class of VMware Cloud Foundation 9.1 incident against the **VCF Operations API**
(`suite-api`) — not the separate Log Management API.

## Incident

Members of several Active Directory groups lost their VCF Operations
permissions overnight. The identity provider is still reachable and users can
still authenticate, so the failure is not a login outage. The appliance holds
two independent records of what happened, and neither alone identifies the
cause:

* the **synchronization logs** of the identity provider's LDAP directories, and
* the **alerts and triggered symptoms** raised on the affected resources.

The directory has been failing to synchronize once an hour for some time. Only
the *first* failing run names the underlying fault; every later run reports a
downstream consequence with a different message key. Reading the newest log
entry, or the alert list alone, produces the wrong root cause.

## Wire contract

`docs/contract.json` is the authority for every request. It is a task-scoped
projection of `specifications/vcf-operations/vcf-operations-openapi.json` from
the Apache-2.0 `vmware/vcf-api-specs` repository at the commit recorded in
`docs/official_sources.json`. Eight operationIds are in scope:

| operationId | method | path |
| --- | --- | --- |
| `acquireToken` | POST | `/api/auth/token/acquire` |
| `getLdapDirectories` | GET | `/api/fleet-management/iam/identity-providers/{idpConfigId}/ldap-directories` |
| `getLdapSyncLogs` | GET | `…/ldap-directories/{ldapDirectoryId}/sync-logs` |
| `getLdapSyncLogById` | GET | `…/sync-logs/{syncLogId}` |
| `queryAlert` | POST | `/api/alerts/query` |
| `getAlertContributingSymptoms` | GET | `/api/alerts/contributingsymptoms` |
| `getSymptoms` | GET | `/api/symptoms` |
| `releaseToken` | POST | `/api/auth/token/release` |

`x-wire-rules` in the contract fixes the request target, the `Authorization`,
`Accept` and `Content-Type` headers, JSON encoding and property order, query
parameter order and serialization, and — the rule this task is built around —
that **an optional field the caller did not set is absent from the request, not
sent as an empty value**.

## Triage procedure

`vcfops_triage.triage.diagnose` runs the following, in this order, against a
single acquired session:

1. `acquireToken` with the supplied credentials.
2. `getLdapDirectories` for `idp_config_id`. Select the one directory whose
   `lastSyncStatus` is `FAILED`. It is not necessarily first in the collection.
3. `getLdapSyncLogs` for that directory, page by page from page `0` with
   `page_size` = `triage.SYNC_LOG_PAGE_SIZE`, until the number of accumulated
   entries reaches `pageInfo.totalCount`. Stop rather than loop if a page
   returns no entries. The `last` parameter is not used.
4. `getLdapSyncLogById` for the **earliest** entry (smallest `timeStamp`) whose
   `success` is `false`. The list operation returns summaries; `syncResult` and
   `syncDetails` come only from this detail call.
5. `queryAlert` with `activeOnly` set to `true` and `alertCriticality` set to
   `triage.ALERT_CRITICALITY`. No other query property is set, and neither
   `page` nor `pageSize` is sent.
6. `getAlertContributingSymptoms` for **all** returned alert identifiers, in the
   order the alerts were returned, in a **single** request.
7. `getSymptoms` once per distinct alert `resourceId`, in the order the resource
   first appears in the alert collection, with `activeOnly` and
   `includeAlarmInfo` both `true`. `alarmInfo` is only served when
   `includeAlarmInfo` is `true`.
8. `releaseToken` — always, including when an earlier step failed. A failure is
   re-raised as `OperationsError` after the session is released.

### Correlation

The **incident alert** is the unique alert that has a contributing symptom whose
`message` contains the failed directory's `name`. Two alerts share a resource,
so the resource identifier alone does not identify it.

`Diagnosis.contributing_symptom_ids` are the incident alert's contributing
symptom identifiers in API order, and `symptom_evidence` holds the matching
symptom records in the same order. `first_failure` describes the earliest failed
synchronization run, and `root_cause_message_key` is its
`syncResultMessageKey`.

## Verification

`python3 -B verify.py` starts the contract-pinned mock on an ephemeral
`127.0.0.1` port, or falls back to request-level dispatch where the sandbox
forbids sockets. The mock derives its route table from `docs/contract.json`,
refuses any operation the contract does not name, and appends every request to a
JSONL log that the suite reads back to assert the exact wire shape. No live
VMware endpoint is contacted and only dummy credentials are used.
