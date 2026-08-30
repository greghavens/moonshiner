# vCenter cluster software remediation client

A small Java client for the vSphere Automation API of a VMware Cloud Foundation 9.0 vCenter Server.
It kicks off a cluster software remediation and follows the resulting task to completion.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The three API operations this client may use, with their paths, query parameters, request bodies, status codes and security schemes. |
| `docs/official_sources.json` | Where the contract came from: repository, tag, commit sha, spec path and digest. |
| `src/com/example/vcf/VcenterRemediationClient.java` | The client. This is the only file you implement. |
| `harness/` | Test harness: the contract-pinned mock appliance, the wire verifier and `TestMain`. Read-only. |
| `run_tests.sh` | Compiles everything and runs the scenarios. |

## Running

```sh
./run_tests.sh
```

The harness starts a mock vCenter on the loopback interface, hands the client its address, and
checks the requests that arrive. Nothing contacts a real appliance. Each scenario's request log is
written to `build/request-logs/<scenario>.json` if you want to see exactly what went over the wire.

## The flow

1. `Cis.Session_create` — POST `/api/session` with HTTP Basic credentials, answered with `201` and
   a session token.
2. `Esx.Settings.Clusters.Software_apply$Task` — POST
   `/api/esx/settings/clusters/{cluster}/software?action=apply&vmw-task=true`, answered with `202`
   and a **task identifier**. The remediation has only been started at this point.
3. `Cis.Tasks_get` — GET `/api/cis/tasks/{task}` until `status` reaches `SUCCEEDED` or `FAILED`.

`PENDING`, `RUNNING` and `BLOCKED` are all states a task can move back out of.
