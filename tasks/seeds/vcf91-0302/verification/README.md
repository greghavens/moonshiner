# VCF Operations for Networks — application inventory sweep

A small Java client that walks the full application inventory of a **VCF Operations for Networks**
appliance (VMware Cloud Foundation 9.1, the successor to vRealize Network Insight) and returns a
summary row per application.

## Layout

```
docs/contract.json           the four operations this client is allowed to call
docs/official_sources.json   where those operations were derived from
src/  com/vmware/vcfops/networks/NetworkInsightInventoryClient.java   the client
      com/vmware/vcfops/networks/Json.java                            JSON helper
test/ com/vmware/vcfops/networks/test/MockNiServer.java               loopback appliance
      com/vmware/vcfops/networks/test/RecordedRequest.java            one logged request
      com/vmware/vcfops/networks/test/ContractVerifier.java           wire-shape assertions
      com/vmware/vcfops/networks/test/TestMain.java                   harness entry point
run_tests.sh                 compile + verify
```

`NetworkInsightInventoryClient.java` is the only file intended to change. Everything under
`test/`, plus `Json.java` and both files in `docs/`, is fixed project scaffolding and is marked
`DO NOT MODIFY`.

## Running

```
./run_tests.sh
```

Requires a JDK (17 or newer); there is no build tool and no third-party dependency. The harness
starts `MockNiServer` on an ephemeral **loopback** port, hands the client its base URL, and then
replays the recorded requests through `ContractVerifier`. Nothing outside `127.0.0.1` is
contacted. Exit code `0` means every assertion held. The full request log is also written to
`build/request-log.jsonl` after each run.

## The contract

`docs/contract.json` is derived from `specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml`
in [vmware/vcf-api-specs](https://github.com/vmware/vcf-api-specs) (Apache-2.0), pinned at commit
`c3f3b52c845dd967cabbc21680e893292077d5ba` — spec `info.version` 9.1.0.0. It names four
operationIds and the mock serves only those; a request to anything else comes back `404` and the
verifier reports it.

| operationId          | method + path                          | notes |
| -------------------- | -------------------------------------- | ----- |
| `create`             | `POST /api/ni/auth/token`              | unauthenticated; returns `{token, expiry}` |
| `listApplications`   | `GET /api/ni/groups/applications`      | `size` (pinned to 5), optional `cursor`, optional `modifiedAfter` |
| `getApplicationById` | `GET /api/ni/groups/applications/{id}` | optional `fetch_member_counts`, optional `fetch_update_status` |
| `delete`             | `DELETE /api/ni/auth/token`            | `204` on success |

Authentication is **not** Bearer. Per the description on `create`:

> All API requests must provide the auth token in Authorization header in following format:
> `Authorization : NetworkInsight {token}`. If a token is invalid or expired, 401-Unauthorized
> error gets returned in the response of the API request. There is limit of 100 valid tokens per
> user and further requests will return 401-Unauthorized. So, users are advised to delete the
> tokens after use.

Two wire rules from `docs/contract.json` are worth calling out because they are asserted
byte-for-byte:

- **Unset optionals are omitted, never sent empty.** An optional query parameter or body field
  with no value must not appear on the wire at all. `cursor=`, `modifiedAfter=`, or
  `"value": ""` inside `domain` are contract violations, not harmless no-ops. `Domain.value` in
  particular is documented in the spec as "not required for LOCAL domain".
- **No fields outside the operation.** Requests carry only the parameters and body keys the
  operation lists.

## Token lifetime

The appliance revokes tokens on its own schedule and the `expiry` timestamp in the token response
is advisory only — a token can stop working before the clock says it should. `401` is the
authoritative signal that a token is finished.

On a lab appliance with a handful of applications a sweep usually completes inside one token's
life, which is why this has not bitten us before. On a populated appliance it does not.
