# SDDC-51782 — overnight ESXi password rotation reported a failure

## Context

We run VMware Cloud Foundation 9.0. A scheduled password rotation ran against the
ESXi hosts of one of our workload domains last night and SDDC Manager reports the
operation as failed. The banner in the UI says only *"Password rotation completed
with failures for one or more resources"*, which tells us nothing we can act on.

We do not want another engineer clicking through the UI at 02:00. Build a small
diagnostic client we can run against SDDC Manager that collects the evidence and
writes a machine-readable verdict.

Two things matter to whoever picks this up:

* **The top-level task does not contain the cause.** Neither does the summary
  list of subtasks. You have to pull the per-subtask detail *and* the system
  notifications SDDC Manager raised around the time of the failure, and line them
  up against each other. The first plausible-looking warning is not automatically
  the answer — several unrelated warnings were raised in the same window, and one
  of them is the same kind of warning about a different domain entirely.
* **Our SDDC Manager is strict about request shape.** It rejects requests that
  carry properties or query parameters the 9.0 API does not define, and it rejects
  properties sent as `null` or as an empty string. Send the fields you actually
  mean and leave everything else out.

## What to build

A single Java source file, `src/VcfDiagnostics.java`, class `VcfDiagnostics`, JDK
standard library only (no Maven/Gradle, no third-party jars). Do not add extra
Java source files — the client must stay self-contained.

It must expose exactly this entry point, which the test harness calls:

```java
public static java.nio.file.Path diagnose(String baseUrl,
                                          String username,
                                          String password,
                                          java.nio.file.Path outDir) throws Exception
```

It performs the investigation described below and writes `outDir/diagnosis.json`,
returning the path to that file.

## The API

`docs/contract.json` is authoritative. It is derived from the VCF 9.0.0.0 SDDC
Manager OpenAPI specification (provenance in `docs/official_sources.json`) and
lists the seven operations available to you, their paths, every query parameter
they declare and every request-body property they accept. Nothing outside that
file is served.

Note the version. Some properties that exist in the VCF 9.1 revision of this API
do not exist in 9.0, and sending them is an error.

## Required investigation

1. **Authenticate** with `createToken` using the supplied username and password.
   Put the returned `accessToken` in `Authorization: Bearer <accessToken>` on
   every later call.

2. **Find the failed rotation** with `getTasks`. Narrow it server-side rather than
   downloading everything and filtering in Java: ask for task status `Failed`,
   task type `CREDENTIALS_ROTATE`, and a limit of `50`. Send those three query
   parameters and no others. Exactly one task will match.

3. **Pull the rotation detail** for that task id with `getCredentialsTask`. This
   gives you the subtask roster and each subtask's status.

4. **Pull the detail of each subtask that did not succeed** with
   `getCredentialsSubTask`. Call it only for the subtasks whose status is not
   `SUCCESSFUL` — do not fetch the ones that worked.

5. **Pull the system notifications** with `getNotifications`. It declares no query
   parameters, so send none. Correlate the notifications against the hosts whose
   subtasks failed and against the domain those hosts belong to. The notification
   that explains the failure is the one that concerns *those* hosts.

6. **Collect a support bundle** for the ticket with `startSupportBundle`. Request
   the ESXi logs and the SDDC Manager logs, and nothing else, scoped to the single
   workload domain the failed hosts belong to, identified by its domain name as it
   appears in the notification. Do not set cluster names, do not set the free-host
   flag, do not send the bundle options object, and do not send the fourteen other
   log flags — not even as `false`.

7. **Wait for the bundle** with `getSupportBundleStatus`, polling the id returned
   in step 6 roughly once a second until its status is no longer `IN_PROGRESS`.
   Give up after 30 attempts.

## Output — `out/diagnosis.json`

A JSON object with exactly these keys:

| key | value |
| --- | --- |
| `taskId` | id of the failed rotation task found in step 2 |
| `taskType` | its task type |
| `credentialsTaskStatus` | `status` returned by `getCredentialsTask`, verbatim |
| `failedSubtaskIds` | ids of the subtasks that did not succeed, sorted ascending |
| `affectedHosts` | `resourceName` of each of those subtasks, sorted ascending |
| `domainName` | name of the domain the affected hosts belong to |
| `rootCauseNotificationType` | `type` of the notification that explains the failure |
| `rootCauseSummary` | one sentence, in your own words, stating the actual cause |
| `supportBundleId` | id returned by `startSupportBundle` |
| `supportBundleStatus` | terminal status observed in step 7 |

`rootCauseSummary` must describe what genuinely went wrong. The error text on the
failed subtasks blames the network; if your summary just repeats that, you have
not finished the investigation.

## Running it

```sh
./run_tests.sh
```

This starts the mock SDDC Manager on a loopback port, compiles `VcfDiagnostics`
together with the harness, runs `TestMain`, and then runs the verifier over the
mock's recorded request log and your `out/diagnosis.json`. No real VMware endpoint
is contacted at any point.

The mock answers a bad request with HTTP 400 and a message naming the exact
violation. Read it — it is quoting `docs/contract.json` back at you.
