# docs/ — what the two JSON files must contain

Both files are written by hand from the OpenAPI document, not generated from the mock.

## contract.json

```json
{
  "specVersion": "<info.version of the spec you read>",
  "taskStatusValues": ["...", "..."],
  "operations": [
    {
      "operationId": "...",
      "method": "GET|POST|PATCH|PUT|DELETE",
      "path": "/v1/... (the OpenAPI path template, braces and all)",
      "successStatus": 200,
      "queryParameters": [{ "name": "...", "required": false }],
      "requestBodySchema": "TokenCreationSpec | string | null",
      "requestBodyFields": [{ "name": "...", "required": false }]
    }
  ]
}
```

- `operations` covers exactly the operations this client calls — no more, no fewer.
- `successStatus` is the single 2xx response code the operation declares.
- `queryParameters` lists every query parameter the operation declares, whether or not the client
  uses it, with `required` copied from the spec.
- `requestBodySchema` is the name of the schema the request body `$ref`s. When the body is an
  inline primitive, use its JSON type name. When the operation has no request body, use `null`.
- `requestBodyFields` lists the top-level properties of that schema, with `required` copied from the
  spec. Use `[]` when there is no body or the body is a primitive.
- `taskStatusValues` holds the values enumerated for `Task.status`. That spec field states them in an
  `example` string that repeats several of them in prose capitalisation — record only the
  `SCREAMING_SNAKE_CASE` spellings, once each. Order does not matter.

## official_sources.json

```json
{
  "repository": "https://github.com/vmware/vcf-api-specs",
  "license": "Apache-2.0",
  "tag": "...",
  "commitSha": "<40 lowercase hex characters>",
  "specPath": "specifications/.../....json",
  "specVersion": "...",
  "operationIds": ["...", "..."]
}
```

`commitSha` is the commit that the tag points at in that repository — the full 40-character id, not
an abbreviation and not a blob or tree id.
