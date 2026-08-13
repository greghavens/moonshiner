# vcfa-deployment-sync

A small Java integration against **VCF Automation** in VMware Cloud Foundation 9.1 — the successor
to vRealize / Aria Automation. It lists the deployments in an org and applies metadata edits to
them.

## Why there is a hand-written contract

VCF Automation ships no OpenAPI document in `vmware/vcf-api-specs`. The wire format this project
codes against was transcribed by hand from the VCF Automation xAPIs reference pages at
developer.broadcom.com.

- `docs/contract.json` — the transcribed contract: paths, field names, requiredness, defaults, plus
  the wire rules this project holds itself to. It states plainly that its source is reference
  documentation rather than a published specification.
- `docs/official_sources.json` — every reference page that was read, the operation it backs and the
  date it was fetched.

Three operations are pinned:

| operation | method | path |
| --- | --- | --- |
| `exchangeRefreshToken` | POST | `/csp/gateway/am/api/login/oauth` |
| `listDeployments` | GET | `/deployment/api/deployments` |
| `patchDeployment` | PATCH | `/deployment/api/deployments/{deploymentId}` |

## Layout

```
src/com/broadcom/vcfa/VcfAutomationClient.java   the client  (this is the file you change)
src/com/broadcom/vcfa/Json.java                  minimal JSON reader/writer, provided
harness/com/broadcom/vcfa/MockVcfAutomation.java loopback appliance pinned to the contract
harness/com/broadcom/vcfa/TestMain.java          drives the client against the mock
verify.sh                                        compile, run, check the wire traffic
```

`Json.java`, the two files under `harness/` and the two files under `docs/` are fixed. The verifier
restores them from `verify/pristine/` before it compiles, so editing them cannot change the result.

## Running it

```
./verify.sh
```

It writes the mock's request log to `run/requests.jsonl` and the harness's observations to
`run/result.json`. Both are useful when something does not line up — the log records the exact
method, path, query string, `Authorization` header and raw request body of every call.

Requires a JDK (`javac`, `java`) and `python3` on `PATH`. Nothing is downloaded and no VMware
endpoint is contacted.
