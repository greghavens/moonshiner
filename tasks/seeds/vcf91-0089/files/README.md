# VCF 9.1 asynchronous vCenter clone

Complete `src/VcfVCenterAutomation/VcfVCenterAutomation.psm1`. The surrounding
files are the protected contract, loopback mock, and acceptance verifier.

The module manifest deliberately requires the preinstalled
`VMware.Sdk.Vcf.SddcManager` PowerCLI module. Do not install or vendor VMware
modules. That prerequisite supplies the VMware OpenAPI connection types used by
the public API.

## Public commands

`New-VcfVCenterClient` has two parameter sets:

- `-Connection` accepts an authenticated
  `VMware.Sdk.OpenApi.Cmdlets.IServerConnection`. `-Server` may override the
  connection's server URI.
- `-Server -SessionToken` creates a client that sends the contract's
  `vmware-api-session-id` header.
  `-SkipCertificateCheck` applies only to this parameter set.

`Invoke-VcfVmClone` accepts a client plus mandatory `-SourceVm` and `-Name`.
`-Folder`, `-ResourcePool`, `-Host`, `-Cluster`, `-Datastore`, and `-PowerOn`
are optional clone fields. It also accepts `-TimeoutSeconds` and
`-PollIntervalMilliseconds`.

On success, `Invoke-VcfVmClone` returns an object with `TaskId`, `Status`,
`Result`, and `PollCount`. It must throw if the task fails, reports an unknown
state, times out, or any HTTP request fails.

## Contract rules

Use only the operations in `docs/contract.json`.

1. POST the clone to
   `/api/vcenter/vm?action=clone&vmw-task=true`.
2. Treat the JSON string returned with HTTP 202 as a task identifier.
3. GET `/api/cis/tasks/{task}` repeatedly. Do not add the optional `spec` query.
4. Continue through `PENDING`, `RUNNING`, and `BLOCKED`; stop successfully only
   at `SUCCEEDED`, and throw at `FAILED`.

The clone body always contains `source` and `name`. Add `placement` only when a
placement parameter is bound, and put only bound placement members inside it.
Add `power_on` only when `-PowerOn` is bound. Never serialize unset optional
fields as null, empty strings, empty objects, or empty arrays.

Every request must send the vCenter session token in
`vmware-api-session-id` and accept JSON.

The verifier starts a loopback-only mock and never contacts a VMware endpoint:

```sh
python3 tests/verify.py
```
