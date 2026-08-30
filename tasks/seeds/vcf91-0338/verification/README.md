# VCF Automation 9.1 deployment triage client

A production deployment in VMware Cloud Foundation Automation 9.1 is stuck in
`UPDATE_FAILED`. `src/VcfaTriage.java` is the on-call client that has to work
out *why* and file the correct day-2 remediation, without a human reading the UI.

## Layout

| Path | Role |
| --- | --- |
| `src/VcfaTriage.java` | **The only file you change.** Single-file Java client. |
| `docs/contract.json` | The API contract. Seven operations, transcribed by hand. |
| `docs/official_sources.json` | Where each operation came from, and when. |
| `harness/TestMain.java` | Harness entry point. Protected. |
| `harness/Json.java` | Minimal JSON reader you may use. Protected. |
| `mock/vcfa_mock.py` | Loopback mock of the API, pinned to the contract. Protected. |
| `mock/fixtures.json` | The environment the mock serves. Protected. |
| `verify/verify.py` | Grades the report and the request wire shape. Protected. |
| `run_tests.sh` | Compile, run, verify. Protected. |

```
./run_tests.sh
```

Everything runs on 127.0.0.1 against the stock JDK and CPython. No third-party
libraries, no build tool, no network.

## The contract

`docs/contract.json` is the authority for every request you make. Read its
`sourceStatement` before you start: VCF Automation ships no OpenAPI document,
so this contract was transcribed from Broadcom's xAPIs reference pages, which
are recorded with their fetch dates in `docs/official_sources.json`. A field or
parameter that is absent from the contract was not transcribed — do not invent
one, and do not send one.

The mock serves exactly the seven operations the contract names. Anything else
is a 404, the same answer the real service gives.

### Wire discipline

`conventions.optionalParameters` in the contract is binding and the verifier
enforces it:

* An optional query parameter or body field for which you have no value is
  **omitted from the wire entirely**. Never an empty string, never `{}`, never
  `null`, and never a server default you did not actively choose.
* Every request carries `Authorization: Bearer <token>` and
  `Accept: application/json`.
* The submit operation defines no query parameters, and its body carries no
  field beyond the three the contract documents.

## What the client must do

`VcfaTriage.triage(deploymentId)` returns the report described below. Getting
there means following the evidence — the failure cause is not in the deployment
summary, and it is not in any event's `details` string. It is in the logs.

1. **Fetch the deployment.** Note its `name` and `status`.
2. **Find the failed request.** List the deployment's requests and take the most
   recent one whose `status` is `FAILED` and whose `dismissed` is `false`. A
   dismissed failure has already been triaged and closed; it is not yours, even
   if it is newer and even if it is the deployment's `lastRequest`.
3. **Find the failing event.** Read *every* page of that request's events and
   take the last event, by position, whose `hasLogs` is `true`. Stop paging when
   the envelope says you are on the last page.
4. **Read its logs.** The log endpoint returns slices. Keep reading, resuming
   from the row after the last one you received, until an entry reports
   `eof: true`. You have no row number for the first call.
5. **Identify the root cause**: the first log row at `ERROR` severity. Its
   `rownum` and its verbatim `message` go in the report. Rows at `INFO` and
   `WARN` are context, not causes — several of them are misleading on purpose.
6. **Resolve the resource.** The failing event names a resource; list the
   deployment's resources to get that resource's id.
7. **Pick the action.** Fetch the actions available on *that* resource, and only
   that one. Apply the runbook below to the root cause. Submit only an action
   whose `valid` is `true`.
8. **Submit it** as a resource action request. Exactly one submission — this is
   a live change on a production tier. The `reason` must name the specific
   object the `ERROR` row identifies, so the audit trail is grounded in the log
   evidence.

### Runbook

Map the `ERROR` root cause to exactly one resource action, matched on the
action's `name`:

| The root cause reports | Submit the action named |
| --- | --- |
| an existing or active snapshot blocking the operation | `Delete Snapshot` |
| a storage policy that is non-compliant or out of capacity | `Resize` |
| guest customization failure, or an unreachable DNS or network endpoint | `Reboot` |

If the chosen action's `schema` declares no properties, it takes no inputs.

## Report format

Nine lines, `\n`-separated, in this order, no leading or trailing spaces:

```
deployment=<name> status=<deployment status>
failedRequest=<request id>
failedEvent=<event id>
failedResource=<resource name from the failing event>
rootCauseRow=<rownum of the first ERROR row>
rootCause=<that row's message, verbatim>
remediationResource=<resource id>
remediationAction=<action id you submitted>
submittedRequest=<id from the submit response> status=<its status>
```
