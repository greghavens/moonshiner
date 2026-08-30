# vcf_onboard — rack onboarding against VCF 9.0 SDDC Manager

`vcf_onboard` drives one multi-step change against a VMware Cloud Foundation 9.0
SDDC Manager appliance: it creates a network pool, attaches an extra IP pool to one of
that pool's networks, validates a set of ESXi host commission specifications and then
commissions those hosts. The change is not transactional. When a later step fails, the
earlier steps have already taken effect on the appliance, and the report this tool
writes is the only record an operator has of what is now true.

Only `vcf_onboard/` is yours to write. `docs/`, `fixtures/` and `tests/` are protected.

## Source of truth

`docs/contract.json` is the contract. It was derived from
`specifications/sddc-manager/sddc-manager-openapi.json` in the Apache-2.0
`vmware/vcf-api-specs` repository at tag `9.0.0.0`, commit
`85151f6b1bb58f13b6ac0304bfec53904bea085f`; `docs/official_sources.json` records the
provenance. Rendered documentation portals and the 9.1.0.0 revision of the same file
are not sources — the 9.1 revision differs, for example by relaxing `Network.required`
and adding properties that do not exist in 9.0.

Use exactly these six operationIds:

| step | operationId | request |
| --- | --- | --- |
| `authenticate` | `createToken` | `POST /v1/tokens` → 201 `TokenPair` |
| `create_network_pool` | `createNetworkPool` | `POST /v1/network-pools` → 201 `NetworkPool` |
| `add_ip_pool` | `addIpPoolToNetworkOfNetworkPool` | `POST /v1/network-pools/{id}/networks/{networkId}/ip-pools` → 200 `Network` |
| `validate_hosts` | `validateHostCommissionSpec` | `POST /v1/hosts/validations` → 202 `Validation` |
| `commission_hosts` | `commissionHosts` | `POST /v1/hosts` → 202 `Task` |
| (polling) | `getTask` | `GET /v1/tasks/{id}` → 200 `Task` |

## Command line

```
python3 -m vcf_onboard --base-url URL --plan PATH --report PATH
                       [--poll-interval SECONDS] [--poll-timeout SECONDS]
```

`--poll-interval` defaults to `10.0` and `--poll-timeout` to `3600.0`. SDDC Manager
credentials come from the environment: `VCF_SDDC_USERNAME` and `VCF_SDDC_PASSWORD`.

Exit `0` when every step succeeded, `2` when the change ran but a step failed, and `1`
for a usage or environment error such as a missing plan file. The report is written in
every case where at least one step was attempted.

## Wire rules

* `application/json` for both `Content-Type` and `Accept`.
* Every request except `createToken` carries `Authorization: Bearer <accessToken>`
  using the `accessToken` from the `TokenPair`. `createToken` is sent unauthenticated.
* `createToken` sends only the credential fields that are set. `apiKey` and `idToken`
  are not in use here, so they do not appear on the wire.
* **An optional field that has no value is omitted.** It is never sent as `null`, `""`,
  `[]`, `{}`, `0` or `false`. This applies at every level: a network with no IP pools
  has no `ipPools` key, a host with no SSH thumbprint has no `sshThumbprint` key.
* Server-assigned properties are never sent in a request body: `NetworkPool.id`,
  `NetworkPool.hostsCount`, `Network.id`, `Network.freeIps`, `Network.usedIps`. See
  `serverAssigned` in each contract schema.
* `vlanId` and `mtu` are JSON numbers, not strings.
* Ids are taken from responses, never guessed. `networkPoolId` on each host
  specification and the `{id}`/`{networkId}` path segments of the IP pool request come
  from the `createNetworkPool` response.
* None of these operations declares a query parameter, so no request carries a query
  string.
* `getTask` requests are bodyless.
* Do not call anything outside the six operations above. In particular the validation
  result is terminal in its first response — there is no validation polling route in
  this contract — and there is no rollback: a step that already succeeded stays.

## Plan file

`fixtures/onboarding_plan.json` holds the change to apply:

* `networkPool` is sent to `createNetworkPool` as-is, minus anything the contract
  forbids on a create.
* `additionalIpPool` names a `networkType`; attach `{start, end}` to the network of
  that type in the created pool, using the id SDDC Manager assigned to it.
* `hosts` become `HostCommissionSpec` items. Each host in the plan supplies its own
  fields; add `networkPoolId` from the created pool. The same list is sent to
  `validateHostCommissionSpec` and then, unchanged, to `commissionHosts`.

Validation is fatal only when `resultStatus` is `FAILED`; a `WARNING` check does not
stop the change but must be reported.

## Commissioning outcome

`commissionHosts` returns 202 with a `Task`. Poll `getTask` on `--poll-interval` until
the status is terminal: `IN_PROGRESS` and `PENDING` are not terminal, `SUCCESSFUL` is
success, `FAILED` and `CANCELLED` are failure. The task's `subTasks` carry the per-host
outcome; each sub-task identifies its host through `resources[0].fqdn`, and a sub-task
that reached `SUCCESSFUL` carries the `resourceId` SDDC Manager assigned to that host.
A partly failed task is normal: some hosts are commissioned, one fails, the rest stay
`PENDING`. Report exactly that, do not flatten it into "commissioning failed".

## Report

Write JSON to `--report`:

```json
{
  "schemaVersion": "vcf-onboard/report/1",
  "planName": "rack-b01-expansion",
  "sddcManager": "<the --base-url value>",
  "overallStatus": "SUCCEEDED | FAILED",
  "steps": [ ... exactly five, in the order of the table above ... ],
  "hosts": [ ... one per host in plan order, empty if commissioning never ran ... ],
  "completedChanges": [ ... ],
  "failure": { ... omitted when overallStatus is SUCCEEDED ... }
}
```

Each step is `{"name", "operationId", "status", "details"}` with `status` one of
`SUCCEEDED`, `FAILED` or `NOT_ATTEMPTED`. All five steps are always listed, including
the ones that never ran. Step details:

| step | `details` keys |
| --- | --- |
| `authenticate` | (none required) |
| `create_network_pool` | `networkPoolId`, `networkPoolName`, `networkIds` (network type → id) |
| `add_ip_pool` | `networkPoolId`, `networkId`, `start`, `end` |
| `validate_hosts` | `validationId`, `executionStatus`, `resultStatus`, `warnings` (descriptions of the `WARNING` checks) |
| `commission_hosts` | `taskId`, `taskStatus` (verbatim from the task) |

`hosts` holds `{"fqdn", "status"}` per host in plan order, plus `resourceId` for a host
that was commissioned and `errorCode`/`message` for one that failed. `status` is the
sub-task status verbatim.

`completedChanges` lists only what actually exists on the appliance now, in the order
it was made:

```json
[
  {"kind": "NETWORK_POOL", "id": "...", "name": "..."},
  {"kind": "IP_POOL", "networkPoolId": "...", "networkId": "...", "start": "...", "end": "..."},
  {"kind": "HOST", "fqdn": "...", "resourceId": "..."}
]
```

One `HOST` entry per host whose sub-task reached `SUCCESSFUL`. A step that failed or
never ran contributes nothing.

`failure` is `{"step", "operationId", "message"}` plus `errorCode` and `taskId` when
the appliance supplied them, and `httpStatus` when the failure was an HTTP error
response.

Never write a password into the report, stdout or stderr.

## Verify

```
python3 -B tests/verify.py
```

The verifier starts a contract-pinned mock SDDC Manager on an ephemeral `127.0.0.1`
port, runs `python3 -m vcf_onboard` against it twice, and asserts the exact request
sequence, headers and body bytes from the mock's request log along with the accuracy of
the report. No live VMware endpoint is contacted. The mock serves only the six contract
operations and rejects any request that the 9.0 schemas would reject, so a shape error
shows up as a `4xx` in the log rather than a passing run.
