# VCF 9.1 vCenter asynchronous clone client

Implement `src/VCenterCloneClient.java` as a dependency-free, single-file
Java client for the three vSphere Automation API operations selected in
`docs/contract.json`. The contract was projected from VMware's VCF 9.1
`vcenter.yaml` OpenAPI specification. Its immutable provenance and exact
operation IDs are recorded in `docs/official_sources.json`.

Do not change the public constructor, record, interface, or method signature.
Use only the JDK. `TestMain` exercises the class against an IPv4 loopback HTTP
mock; verification never contacts a VMware endpoint.

`cloneWaitAndList` must:

1. Reject a null or blank source VM identifier or clone name, a non-positive
   timeout, or a negative poll interval before sending a request.
2. Send exactly one `POST` to
   `/api/vcenter/vm?action=clone&vmw-task=true`. The compact JSON body contains
   exactly `source` and `name`, in that order, with correct JSON string
   escaping. Accept exactly HTTP 202 and parse the response JSON string as a
   nonblank task identifier.
3. For every request send `Accept: application/json` and
   `vmware-api-session-id: <constructor token>`. Send
   `Content-Type: application/json` only for the POST. Do not send
   `Authorization`, a body on GET, or undeclared query parameters.
4. Poll `GET /api/cis/tasks/{task}`. Percent-encode the complete task
   identifier as one RFC 3986 UTF-8 path segment. Continue after `PENDING`,
   `RUNNING`, or `BLOCKED`, invoking the supplied `Sleeper` between polls.
   Proceed only after `SUCCEEDED`; fail on `FAILED` or any unknown status.
5. Enforce the supplied overall timeout using a monotonic clock. Each HTTP
   request must use only the remaining budget. Propagate interruption. Reject
   malformed success payloads and report an unexpected HTTP status in an
   `IOException`.
6. Only after task success, send one bodyless `GET /api/vcenter/vm` with no
   query. Parse every result's required `vm`, `name`, and `power_state`
   strings into `VmSummary`.
7. Return an immutable collection sorted locally by ascending `name`, breaking
   ties by `vm`. Never trust server order.

The mock returns `PENDING`, `RUNNING`, and then `SUCCEEDED` for every clone. It
refuses a collection request while any clone task is incomplete. On each list
response it flips the order relative to its previous orientation. Its
newline-delimited, fsynced request log is read by `TestMain` to verify the
contract sequence.

Run:

```sh
python3 -B tests/verify.py
```
