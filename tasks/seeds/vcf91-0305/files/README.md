# VCF Operations for Networks — application onboarding client

A dependency-free, single-file Java client that onboards a multi-tier application
into VMware Cloud Foundation 9.1 **VCF Operations for Networks** (the successor to
vRealize Network Insight).

## Layout

| Path | Role |
| --- | --- |
| `src/AppOnboarder.java` | the client — the only file you edit |
| `config/onboarding.json` | the application and tiers to onboard |
| `docs/contract.json` | the pinned REST contract |
| `docs/official_sources.json` | provenance of that contract |
| `tests/MockVcfOnServer.java` | loopback mock, pinned to `docs/contract.json` |
| `tests/TestMain.java` | harness that runs the client against the mock |
| `tests/verify.py` | verifier |

## REST contract

`docs/contract.json` is derived from the OpenAPI specification at
`specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml` in the
[vmware/vcf-api-specs](https://github.com/vmware/vcf-api-specs) repository
(Apache-2.0), revision `c3f3b52c845dd967cabbc21680e893292077d5ba`
(spec `info.version` 9.1.0.0). Five operationIds are in play:

| operationId | method | path |
| --- | --- | --- |
| `create` | POST | `/api/ni/auth/token` |
| `addApplication` | POST | `/api/ni/groups/applications` |
| `addTier` | POST | `/api/ni/groups/applications/{id}/tiers` |
| `listApplicationTiers` | GET | `/api/ni/groups/applications/{id}/tiers` |
| `delete` | DELETE | `/api/ni/auth/token` |

Authentication uses the spec's `ApiKeyAuth` scheme:
`Authorization: NetworkInsight {token}`.

## Running

```sh
python3 -B tests/verify.py
```

The verifier compiles the client with the mock and the harness, then runs the
partial fixture and a successful variant against fresh loopback mock instances.
It checks both request logs and both reports. No live VMware endpoint is
contacted.

To look at a run by hand:

```sh
javac -d /tmp/classes src/AppOnboarder.java tests/MockVcfOnServer.java tests/TestMain.java
java -cp /tmp/classes TestMain /tmp/out config/onboarding.json
cat /tmp/out/requests.jsonl   # what went on the wire
cat /tmp/out/report.json      # what the client reported
```
