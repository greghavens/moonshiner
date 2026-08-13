# VCF Operations custom-group reconciler

Our fleet automation runs a reconcile step against **VCF Operations** (the
Operations component of VMware Cloud Foundation 9.1 — its own Suite API, not Log
Management) on every deploy. The step guarantees that a named custom group
exists with a given membership rule.

Because the deploy pipeline retries steps, this reconcile has to be **safe to
run twice**. Running it a second time must converge on the same group; it must
not leave two groups behind and it must not fail. The Operations API has no
"upsert" call, so retry safety has to come from how the client sequences the
calls it does have.

Nobody here has written against this API before, so the first half of the job is
pinning down what the wire protocol actually is — from the published OpenAPI
specification, not from a docs page.

## 1. Derive the contract from the specification

The authoritative source is `specifications/vcf-operations/vcf-operations-openapi.json`
in the **`vmware/vcf-api-specs`** repository on GitHub (Apache-2.0), at
immutable commit `c3f3b52c845dd967cabbc21680e893292077d5ba`. Read that
file at that revision and record what you found.

### `docs/contract.json`

```json
{
  "source": {
    "repository": "https://github.com/vmware/vcf-api-specs",
    "specPath": "<path of the spec file inside the repository>",
    "commit": "<40-character lowercase sha of the revision you read>",
    "openapiVersion": "<the spec's own openapi version field>",
    "apiVersion": "<info.version from the spec>",
    "serverBasePath": "<the base path the spec declares under servers>"
  },
  "operations": [
    {
      "operationId": "<operationId exactly as the spec spells it>",
      "method": "<GET|POST|PUT>",
      "path": "<path key as it appears under the spec's paths object>",
      "successStatus": 200,
      "requestSchema": "<component schema name, or null if the operation takes no body>",
      "responseSchema": "<component schema name of the success response>",
      "optionalQueryParameters": ["<name>", "..."]
    }
  ],
  "schemas": {
    "<component schema name>": {
      "required": ["<sorted required property names>"],
      "optional": ["<sorted names of every other declared property>"]
    }
  }
}
```

Rules:

- `operations` holds exactly the operations this client issues — one for
  acquiring an API token, one for reading the existing custom groups, one for
  creating a custom group and one for modifying a custom group. Order does not
  matter. Nothing else belongs in the list.
- `path` is the spec's path key, i.e. *without* the server base path prefixed.
- `successStatus` is the one 2xx status the spec documents for that operation.
  They are not all the same — check each one.
- `optionalQueryParameters` lists the names of the non-required query parameters
  the spec declares for that operation, sorted; use `[]` when there are none.
- `schemas` holds one entry per component schema the client has to build or
  read: the request and response schemas named above, plus every component the
  client instantiates inside a custom-group request body — the resource key, the
  membership definition, a membership rule group, that rule group's resource
  kind key, and the resource-name condition rule it uses. Nine entries in total.
  `required` is the schema's own `required` list; `optional` is every other
  property it declares. Both sorted.

### `docs/official_sources.json`

```json
{
  "specification": {
    "repository": "https://github.com/vmware/vcf-api-specs",
    "license": "Apache-2.0",
    "path": "<same spec path as above>",
    "commit": "<same 40-character sha as above>",
    "title": "<info.title from the spec>",
    "version": "<info.version from the spec>"
  },
  "operationIds": ["<sorted operationIds of the four operations>"]
}
```

## 2. Implement the client

Implement `src/main/java/com/vmware/vcfops/VcfOperationsClient.java`. Keep the
public surface exactly as the stub declares it — `harness/TestMain.java` compiles
against it.

Constraints:

- **One file.** No build system, no third-party dependencies, JDK class library
  only. That includes JSON: hand-roll the little bit of serialising and parsing
  you need.
- Every request goes to `baseUrl` + the server base path from the spec + the
  operation path.
- Requests and responses are JSON. Ask for it and say what you are sending.
- `acquireToken` stores the token it received; every later call authenticates
  with the **most recently acquired** token. The OpenAPI security scheme names
  the `Authorization` header but does not encode its value prefix; VCF
  Operations 9.1 session tokens use the form `OpsToken <token>`.
- **Send only the fields you actually have.** An optional field that the caller
  left unset is *omitted from the JSON body* — not sent as `null`, not sent as an
  empty string, not sent as an empty array or empty object. The same goes for
  query parameters: do not append an optional query parameter just to give it its
  default value. The server rejects bodies that violate this.
- Accept the success status the spec documents for each operation. Treat any
  other status as a failure and throw with the response body included.
- `ensureCustomGroup` must be safe to retry: two identical calls leave exactly
  one group and both return its identifier. `lastAction()` reports `"created"` or
  `"updated"` for the most recent call.

## 3. Run it

```
bash harness/run_tests.sh
```

This starts the loopback mock (bound to `127.0.0.1`, on a free port), compiles
the client with the harness, runs the harness against the mock and then prints
the mock's request log so you can see exactly what went over the wire.

The mock is pinned to `docs/contract.json`: it routes only the operations your
contract names, at the method and path your contract records, and it validates
request bodies against the field sets your contract records. If the contract is
wrong the mock will not behave like VCF Operations. Its error responses carry a
`message` explaining the rejection — read them.

`harness/` is fixed; do not edit anything in it.
