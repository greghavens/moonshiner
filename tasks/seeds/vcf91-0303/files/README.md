# VCF Operations for Networks — application inventory client

A small Java client that pulls the full application inventory out of a **VCF Operations for
Networks** appliance (VCF 9.1; the product formerly shipped as vRealize Network Insight) and
prints it in a stable order.

Everything here runs against a loopback mock. No VMware endpoint is contacted, at build time or
at verification time.

## Layout

| Path | What it is |
| --- | --- |
| `src/VcfOnAppInventory.java` | **The client. This is the file you implement.** |
| `docs/contract.json` | The wire contract, derived from the OpenAPI specification |
| `docs/official_sources.json` | Provenance: spec path, pinned commit sha, operation ids |
| `harness/MockVcfOnServer.java` | Loopback mock pinned to the contract; logs every request |
| `harness/TestMain.java` | Drives the client against two datasets |
| `harness/MiniJson.java` | Dependency-free JSON reader/writer, on the classpath for you |
| `harness/fixtures/*.json` | The mock's datasets |
| `verify/verify.py` | Checks the output *and* the wire shape |
| `run.sh` / `verify.sh` | Build and run; build, run and check |

`docs/`, `harness/` and `verify/` are fixed — treat them as the appliance and the acceptance
test. `src/VcfOnAppInventory.java` is yours.

## The contract

`docs/contract.json` is authoritative and worth reading in full. It is derived from
`specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml` in
[vmware/vcf-api-specs](https://github.com/vmware/vcf-api-specs) (Apache-2.0), pinned at the
`9.1.0.0` revision recorded in `docs/official_sources.json`. Three operations are in play, named
by their spec `operationId`:

| operationId | Request |
| --- | --- |
| `create` | `POST /api/ni/auth/token` — exchange a `UserCredential` for a `Token` |
| `listApplications` | `GET /api/ni/groups/applications` — the paginated collection |
| `getApplicationById` | `GET /api/ni/groups/applications/{id}` — one `Application` |

The mock serves those three and nothing else; anything else is a 404. When the mock rejects a
request it says which rule was broken, so develop against it — `./run.sh` then read
`out/run1/requests.jsonl`.

Points that are easy to get wrong:

- **Pagination.** Send `size` on every request. Do **not** send `cursor` on the first request of
  a walk. On each subsequent request send the previous response's `cursor` verbatim — it is
  opaque. A walk ends when the response has no `cursor`, **or** when its `cursor` is the empty
  string; both happen here and both must be handled. Collect exactly `total_count` ids.
- **Unset optional fields are omitted, never sent empty.** No `cursor=`, no `"domain": null`,
  no `?fetch_member_counts=`. This applies inside nested objects too: a `Domain` with only a
  `domain_type` configured must serialise as `{"domain_type":"LOCAL"}` with no `value` key.
- **`listApplications` returns bare references.** Its `results` entries carry only `entity_id`
  and `entity_type`. The display name and tier count come only from `getApplicationById`.
- **Walk first, then resolve.** Finish every list page before the first detail request. Issue one
  detail request per entity id, in discovery order, exactly once each. Sort afterwards — never by
  reordering requests.
- **Entity ids go into the path verbatim.** They look like `18230:561:271275765`; `:` is a legal
  path character and must not be percent-encoded.
- **Auth.** `Authorization: NetworkInsight <token>`, one space, on every request except `create`.
  `create` itself sends no `Authorization` header.

## Command line

```
VcfOnAppInventory --base-url URL --username U --password P --page-size N \
                  [--domain-type T] [--domain-value V] [--modified-after MS]
```

Parsing is already written. `--modified-after`, `--domain-type` and `--domain-value` are absent
when not configured, and when absent the corresponding wire field must be absent too.

## Output

One line per application, tab-separated, then a total:

```
<entity_id>\t<name>\t<tier_count>
...
total=<count>
```

**Ordering.** Ascending by `name`; ties broken ascending by `entity_id`. Both use Java's
case-sensitive `String.compareTo`. That is deliberate and the fixtures
lean on it: `"Alpha-Portal"` sorts before `"alpha-portal"`, and among equal names
`"18230:561:1000"` sorts before `"18230:561:900"` because `'1'` precedes `'9'`. Do not sort
numerically and do not fold case.

Nothing else may go to stdout; diagnostics belong on stderr.

## Running

```sh
./run.sh      # compile, then drive the client against the mock twice
./verify.sh   # the above, then check output and wire shape
```

`verify.sh` is the acceptance check. It exercises two datasets that differ in page size, in how
the final page ends the walk, in whether `modifiedAfter` is configured and in whether an
authentication domain is configured — so both branches of every optional field get exercised.

Requires a JDK (17+) and Python 3. No external network, no third-party libraries.
