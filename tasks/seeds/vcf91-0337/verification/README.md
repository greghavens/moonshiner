# vcfa-credential-rotator

A single-file Java client that rolls the vSphere service-account secret of a **VCF Automation 9.1**
cloud account over the provisioning (IaaS) API, driven by a loopback test harness.

## Layout

| Path | Role |
| --- | --- |
| `src/CredentialRotator.java` | the client — **the only file you edit** |
| `docs/contract.json` | the API contract this client is held to |
| `docs/official_sources.json` | the reference pages `docs/contract.json` was transcribed from |
| `harness/MockAutomationServer.java` | loopback test double, routed from `docs/contract.json` |
| `harness/WireVerifier.java` | asserts the wire shape of the recorded traffic |
| `harness/TestMain.java` | runs the scenarios |
| `harness/Json.java` | minimal JSON reader/writer, usable from the client |
| `run_tests.sh` | compiles everything and runs the suite |

Run the suite with:

```sh
bash run_tests.sh
```

It needs a JDK 17 or newer and nothing else — no build tool, no third-party jar, no network.

## The contract

`docs/contract.json` is **derived from reference documentation, not from a published
specification**: VCF Automation has no specification in the `vmware/vcf-api-specs` repository, so
the contract was transcribed by hand from the Broadcom xAPIs reference pages recorded in
`docs/official_sources.json`. It names five operations, and those five are the only ones the mock
serves:

- `GET /iaas/api/about`
- `GET /iaas/api/cloud-accounts-vsphere/{id}`
- `PATCH /iaas/api/cloud-accounts-vsphere/{id}`
- `GET /iaas/api/request-tracker`
- `GET /iaas/api/request-tracker/{id}`

Anything else — a login call, a filter parameter that is not documented, a field the update schema
does not declare — is refused by the mock and reported by the verifier.

## Entry point

`TestMain` calls exactly one method:

```java
public static Map<String, Object> rotate(String baseUrl, String bearerToken,
                                         String cloudAccountId, String newPassword) throws Exception
```

`baseUrl` is an origin such as `http://127.0.0.1:53211` with no trailing slash. The returned map
must carry:

| Key | Value |
| --- | --- |
| `apiVersion` | `String` — the version the deployment advertised and the client pinned its calls to |
| `drainedRequestIds` | `List<String>` — ids of the requests that were in progress when the rotation started and were waited on, in the order first observed |
| `requestId` | `String` — id of the request tracker the update returned |
| `status` | `String` — terminal status of that tracker |

`harness/Json.java` is on the classpath and may be used for parsing and serialising; the client
itself stays a single file.

## Where the fixtures come from

Ids, host names, API version strings and passwords used by the mock are seed fixtures, not
documented values — which is why the version in use is discovered at runtime rather than assumed.
The suite runs two successful rotations against deployments that advertise different API versions,
then a third whose update tracker ends in `FAILED`.
