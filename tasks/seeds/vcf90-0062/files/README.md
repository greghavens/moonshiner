# vcfops-alert-triage

Nightly alert triage for a VMware Cloud Foundation Operations 9.0 deployment,
packaged as a PowerShell module. Every call to the deployment goes through the
`VMware.Sdk.Vcf.Ops` PowerCLI module.

```
config/          run configurations
docs/            the REST contract this module is written against, and where it came from
mock/            loopback stand-in for a VCF Operations appliance, plus alert fixtures
src/             the module (this is the part that is not finished)
tools/           regenerates docs/ from the OpenAPI specification
verification/    the acceptance run; treat it as read-only
```

## Prerequisite

`VMware.Sdk.Vcf.Ops` is installed by the environment and must not be vendored
into this repository:

```
pwsh -NoProfile -Command "Install-PSResource -Name VMware.Sdk.Vcf.Ops -TrustRepository"
```

## The contract

`docs/contract.json` is generated from
`specifications/vcf-operations/vcf-operations-openapi.json` at tag `9.0.0.0` of
[vmware/vcf-api-specs](https://github.com/vmware/vcf-api-specs) (Apache-2.0).
`docs/official_sources.json` records the commit, the blob hash and every
operationId. `tools/derive_contract.py` regenerates both from a local copy of
the specification; it refuses to run against any other revision of the file.

The sweep is allowed to touch five operations and nothing else:

| operationId                 | request                          |
| --------------------------- | -------------------------------- |
| `acquireToken`              | `POST /suite-api/api/auth/token/acquire` |
| `getCurrentVersionOfServer` | `GET /suite-api/api/versions/current`    |
| `queryAlert`                | `POST /suite-api/api/alerts/query`       |
| `modifyAlerts`              | `POST /suite-api/api/alerts`             |
| `releaseToken`              | `POST /suite-api/api/auth/token/release` |

`acquireToken` and `getCurrentVersionOfServer` are what `Connect-VcfOpsServer`
issues when a session is established; the other three are called directly.

## Run configuration

```jsonc
{
  "server": "127.0.0.1",              // appliance host
  "protocol": "http",                 // http or https
  "port": 18900,
  "ignoreInvalidCertificate": true,
  "user": "svc-triage",
  "password": "…",
  "authSource": "Local Users",        // optional
  "pageSize": 2,                      // alerts per queryAlert page
  "batchSize": 2,                     // alerts per modifyAlerts call
  "query": {                          // alert-query filter; every key is optional
    "activeOnly": true,
    "alertCriticality": ["CRITICAL", "IMMEDIATE"],
    "alertStatus": ["ACTIVE", "NEW"]
  },
  "assignOwnership": {
    "alertCriticality": "CRITICAL",   // alerts at this level get an owner
    "userAccountId": "…"
  },
  "suspend": { "minutes": 120 }       // everything else is suspended for this long
}
```

## The mock

```
python3 mock/vcfops_mock.py \
    --contract docs/contract.json \
    --scenario mock/scenarios/token-expiry.json \
    --port 18900 --log /tmp/requests.jsonl
```

It binds loopback only, serves exactly the five contracted operations and 404s
anything else. Request bodies and query strings are validated against the
contract, so an optional field sent as `null`, `""` or `[]` comes back as a 400
instead of being accepted. Applying an action to an alert that already has one
comes back as a 409. Every request is appended to the `--log` file as JSON.

The scenarios differ in how long the access token lives:
`mock/scenarios/token-expiry.json` issues a token that the appliance stops
accepting after six authenticated calls; `steady-token.json` and
`unfiltered-query.json` issue one that lasts the whole run.

## Verification

```
python3 verification/run_verification.py
```

It starts the mock on an ephemeral loopback port for each scenario, runs
`verification/harness/Invoke-TriageRun.ps1`, and then asserts the exact shape of
every request the module made. Nothing outside the loopback interface is
contacted. `verification/`, `mock/`, `docs/` and `config/` are fixed inputs.

## Field notes on the SDK

* A `VcfOpsServer` connection is cached per `user@host:port` for the lifetime of
  the PowerShell session. Calling `Connect-VcfOpsServer` a second time with the
  same target hands back the existing connection and does not re-authenticate.
* `Disconnect-VcfOpsServer` releases the access token.
* A rejected access token surfaces as a PowerCLI `VimException` wrapping
  `VMware.Binding.OpenApi.Client.Exceptions.NotAuthenticatedApiException`.
* The `Initialize-VcfOps*` cmdlets build the request models. A model property
  that is never set is left out of the serialized body entirely.
