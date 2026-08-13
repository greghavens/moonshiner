# vcfops-alerts

Read the VMware Cloud Foundation 9.1 Operations alert collection over the
Operations API — its own `suite-api`, not log management — and emit it
completely, once per alert, in a stable order.

Standard library only. No third-party packages, no vendored HTTP client.

## Layout

```
docs/contract.json            the wire contract, derived from the specification
docs/official_sources.json    where the contract came from
src/vcfops_alerts/contract.py loads the contract, builds request targets
src/vcfops_alerts/client.py   the client
src/vcfops_alerts/__main__.py the command
src/vcfops_alerts/errors.py   exceptions (already written)
.protected/                   verification; read it, run it, do not modify it
```

## 1. The contract

`docs/contract.json` is derived from
`specifications/vcf-operations/vcf-operations-openapi.json` in the Apache-2.0
[`vmware/vcf-api-specs`](https://github.com/vmware/vcf-api-specs) repository at
commit `c3f3b52c845dd967cabbc21680e893292077d5ba` — the specification document
itself, not a documentation or reference page. Three operations are used:
`acquireToken`, `getAlerts` and `releaseToken`.

The document has this shape:

```json
{
  "source": {
    "repository": "https://github.com/vmware/vcf-api-specs",
    "specPath": "specifications/vcf-operations/vcf-operations-openapi.json",
    "commit": "<the commit the file was read from>",
    "openapi": "<the document's `openapi` field>",
    "infoVersion": "<the document's `info.version`>"
  },
  "basePath": "<the document's servers[0].url>",
  "securityScheme": {
    "name": "<the key under components.securitySchemes>",
    "type": "<its type>",
    "in": "<where it goes>",
    "headerName": "<its name>",
    "tokenPrefix": "OpsToken"
  },
  "operations": {
    "acquireToken": {
      "method": "POST",
      "path": "/api/auth/token/acquire",
      "security": [],
      "queryParameters": [],
      "requestBody": {
        "required": true,
        "contentType": "application/json",
        "schema": "username-password"
      },
      "responseSchema": "auth-token"
    }
  },
  "schemas": {
    "username-password": {
      "type": "object",
      "required": ["password", "username"],
      "properties": {
        "authSource": {"type": "string"},
        "password": {"type": "string"},
        "username": {"type": "string"}
      }
    }
  }
}
```

Rules for the parts not shown:

* `operations` names exactly `acquireToken`, `getAlerts` and `releaseToken`.
  Each entry carries `method`, `path` (below `basePath`), `security`,
  `queryParameters`, `requestBody` and `responseSchema`. `security` is the list
  of security scheme names that apply to the operation after any per-operation
  override — an operation the specification exempts gets `[]`. `requestBody`
  and `responseSchema` are `null` when the operation declares none.
* `queryParameters` is a list, in specification order, of
  `{"name": ..., "in": "query", "required": <bool>, "schema": {...}}`. OpenAPI
  treats an absent `required` as false; record it explicitly as `false`. The
  `schema` is the parameter's schema as declared, including any `default`.
* `schemas` names exactly `username-password`, `auth-token`, `alerts`,
  `page-info` and `link`. Record each schema's `type`, its `required` list
  (sorted) when it declares one, and its `properties`. For each property record
  only the JSON Schema keywords the specification declares for it — `type`,
  `format`, `enum`, `items`, `minimum`, `default`. Drop `description` and `xml`
  annotations; a property whose *name* happens to be `description` is still a
  property and is kept. Where a property refers to another schema, write
  `{"$ref": "<schema name>"}` — the bare name, not a JSON pointer. The item
  schema of the alert list is `{"$ref": "alert"}`; the `alert` schema itself is
  not recorded.
* `securityScheme.tokenPrefix` is `"OpsToken"`. The specification pins the
  scheme's `type`, `in` and `name`; the appliance requires the token to be
  presented as `Authorization: OpsToken <token>`.

`docs/official_sources.json` records the provenance:

```json
{
  "sources": [
    {
      "repository": "https://github.com/vmware/vcf-api-specs",
      "license": "Apache-2.0",
      "specPath": "specifications/vcf-operations/vcf-operations-openapi.json",
      "commit": "...",
      "blobSha": "<git blob sha1 of the file you fetched>",
      "sha256": "<sha256 of the bytes you fetched>",
      "operationIds": ["acquireToken", "getAlerts", "releaseToken"]
    }
  ]
}
```

The two digests are checked against the real specification bytes, so fetch the
file and compute them; they cannot be guessed. The git blob sha1 is
`sha1("blob " + len(bytes) + "\0" + bytes)`.

## 2. The wire

Every target is built from the contract — `basePath` plus the operation's
`path` plus an encoded query string. No path, parameter name or base path is
written as a literal anywhere in `src/`.

* Every request sends `Accept: application/json`.
* Only a request that carries a body sends `Content-Type: application/json`.
  `releaseToken` has no body: no bytes, and no `Content-Type` header either.
* Operations whose `security` is non-empty send
  `Authorization: OpsToken <token>`. `acquireToken` sends no `Authorization`
  header at all.
* Unset means absent. An optional body property the caller did not supply is
  left out of the JSON object rather than sent as `null` or `""`; an optional
  query parameter the caller did not supply is left out of the query string
  rather than sent as `name=`. An empty string is not a value.
* `id` and `resourceId` are array parameters: one `name=value` pair per value,
  in the order the caller gave them.
* `page` and `pageSize` are sent on every `getAlerts` request, including the
  first — the client chooses them, so they are set values, not defaults to lean
  on.

## 3. The collection

`getAlerts` is paginated. The response carries `alerts`, `pageInfo`
(`page`, `pageSize`, `totalCount`) and `links`.

* Read pages from `page` 0 upwards until the collection is exhausted. Use
  `pageInfo.totalCount` to know when that is: do not request a page beyond the
  last one the appliance says exists, and do not stop early.
* The collection can shift under a paged read, so the same alert may arrive on
  two pages with different content. Deduplicate by `alertId`, keeping the row
  from the earlier page. Rows count towards `totalCount` as received,
  duplicates included.
* Return the alert objects exactly as received, sorted by `startTimeUTC`
  descending, ties broken by `alertId` ascending. The order is total: two runs
  produce the same sequence, byte for byte.

## 4. The API

```python
from vcfops_alerts import OperationsClient, load_contract

contract = load_contract()                   # docs/contract.json by default
contract.base_path                           # "/suite-api"
contract.operation_ids                       # the three operationIds
op = contract.operation("getAlerts")
op.method, op.path, op.security, op.query_parameters
op.request_body, op.response_schema
contract.target("getAlerts", query=[("page", 0), ("pageSize", 10)])
# -> "/suite-api/api/alerts?page=0&pageSize=10"

with OperationsClient(base_url, username=..., password=..., auth_source=None,
                      contract=None, timeout=30.0) as client:
    alerts = client.fetch_alerts(page_size=1000, resource_ids=None,
                                 alert_ids=None)
```

`base_url` is the appliance root without the `/suite-api` prefix — that prefix
is the contract's. Leaving the context manager releases the token; the release
is the last request of the run. A response the client cannot work with raises
`OperationsApiError` carrying its `status` — it is never swallowed.

```
python3 -m vcfops_alerts --base-url URL --username U --password P \
    [--auth-source S] [--page-size N] [--resource-id ID ...] [--alert-id ID ...]
```

prints `json.dumps(alerts, indent=2, sort_keys=True)` and a newline to stdout,
acquiring and releasing a token around the read, and never echoing the
password.

## 5. Verification

```
python3 -B .protected/verify.py
```

starts a loopback mock pinned to your `docs/contract.json` — it routes only the
operations that contract names and serves nothing else — and asserts the exact
wire shape from the mock's request log. No VMware endpoint is contacted.
