# VCF 9.1 NSX Policy realization client

Implement `src/NsxPolicyClient.java` as a dependency-free, single-file Java
client for the three NSX Policy operations selected in
`docs/contract.json`. The contract was extracted from VMware's VCF 9.1
OpenAPI 2.0 specification; provenance and the exact operation IDs are pinned
in `docs/official_sources.json`.

Do not change the public constructor, record, interface, or method signature.
Use only the JDK. `TestMain` exercises the class against a loopback HTTP mock;
the verifier never contacts a VMware endpoint.

`upsertWaitAndList` must:

1. Send a `PUT` to the project-scoped security-policy item path. Encode every
   path segment independently. The JSON body must describe a
   `SecurityPolicy` whose `display_name` is the supplied display name and whose
   `category` is `Application`.
2. Treat only a 2xx response as success. For every request send
   `Accept: application/json` and HTTP Basic authentication from the constructor
   credentials; also send `Content-Type: application/json` for the `PUT`.
3. Poll the project-scoped realized-state status operation with
   `intent_path=/infra/domains/{domain-id}/security-policies/{security-policy-id}`.
   Do not issue the collection request until the response has both
   `consolidated_status.consolidated_status == "SUCCESS"` and
   `publish_status == "REALIZED"`.
4. Continue polling while the consolidated status is `IN_PROGRESS` or
   `SANDBOXED_REALIZATION_PENDING`. Treat every other non-success status as a
   terminal failure. Enforce the supplied overall timeout with a monotonic
   clock, and wait through the supplied `Sleeper` between non-terminal polls.
5. Fetch the project-scoped security-policy collection, parse each result's
   `id` and `display_name`, and return immutable `PolicySummary` values sorted
   locally in ascending `displayName` order, breaking ties by `id`. Do not rely
   on server collection order.
6. Propagate interruption, reject malformed success payloads, and include the
   HTTP status and response body in an `IOException` for non-2xx responses.

The mock deliberately returns `IN_PROGRESS` twice before `SUCCESS`, refuses a
premature collection request, and reverses collection element order on every
other list response. Its newline-delimited request log is read by `TestMain` to
verify the operation sequence.

Run:

```sh
python3 tests/verify.py
```
