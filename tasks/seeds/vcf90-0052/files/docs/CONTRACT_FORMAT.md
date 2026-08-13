# `docs/` file formats

Two files live here. Both are hand-maintained from an upstream OpenAPI document and
are consumed by code, so the shapes below are load-bearing.

---

## `docs/official_sources.json`

Records where the contract came from, precisely enough that a reviewer can fetch the
identical bytes years later.

```json
{
  "sources": [
    {
      "repository": "<https URL of the upstream git repository>",
      "license": "<SPDX identifier of that repository's license>",
      "tag": "<git tag the contract was derived from>",
      "commit_sha": "<full 40-hex commit sha that the tag resolves to>",
      "spec_path": "<repository-relative path of the specification file>",
      "spec_version": "<the info.version value inside that specification file>",
      "url": "<permalink to the specification file at that commit sha>",
      "operation_ids": ["<every operationId the contract names, in the order listed in docs/contract.json>"]
    }
  ]
}
```

A branch name, a `refs/tags/...` ref, or a short sha are not acceptable for
`commit_sha`; it must be the resolved 40-character commit sha.

---

## `docs/contract.json`

The subset of the specification this project depends on, transcribed. Nothing here
may be invented: every field is a transcription of the specification document named
in `official_sources.json`.

```json
{
  "api": "<the info.title value from the specification>",
  "spec_version": "<the info.version value from the specification>",
  "server_base_path": "<the path component of the servers[0].url template>",
  "auth": {
    "scheme": "<the securityScheme name referenced by the operations>",
    "in": "header",
    "name": "<the header name that security scheme carries>"
  },
  "operations": [ <operation>, ... ],
  "schemas": { "<schema name>": <schema>, ... }
}
```

### `<operation>`

```json
{
  "operation_id": "<the operationId, verbatim>",
  "method": "<uppercase HTTP method>",
  "path": "<path template with the query string stripped, e.g. /a/{b}/c>",
  "query": { "<name>": "<value>" },
  "spec_path_key": "<the paths[] key verbatim, query string and all>",
  "path_params": ["<name of each templated path parameter, in order of appearance>"],
  "request_body": "<schema name of the request body, or null when the operation takes none>",
  "success_status": <the non-error HTTP status code documented for the operation>,
  "response_body": "<schema name of the success response body, or null when there is none>"
}
```

`query` is `{}` for operations whose `paths[]` key carries no query string.

### `<schema>`

```json
{ "properties": { "<property name>": <property>, ... } }
```

Property names are the JSON names as they appear on the wire, not prose spellings
used in descriptions.

### `<property>`

```json
{
  "type": "string" | "integer" | "boolean" | "array" | "object",
  "required": true | false,
  "format": "<the OpenAPI format, omit the key when the property has none>",
  "enum": ["<permitted value>", ...],
  "ref": "<schema name, present only when type is \"object\">",
  "items": { "type": "<scalar type>" } | { "ref": "<schema name>" }
}
```

* `required` is `true` when the property appears in the schema's `required` list.
* `enum` is present only for properties whose permitted values the specification
  enumerates. Some vSphere Automation schemas carry no OpenAPI `enum` keyword and
  instead enumerate the permitted values in the property description — transcribe
  those, in the order the specification lists them. Omit the key entirely for
  properties with no enumerated values.
* `items` is present only when `type` is `"array"`.
* `ref` is present only when `type` is `"object"`. A property that the specification
  expresses as an `allOf` wrapping a single `$ref` is an object property.

### Which schemas to include

`schemas` must contain exactly the transitive closure of the schemas reachable from
the `request_body` and `response_body` of the listed operations — no more, no fewer.
Error-response schemas are out of scope.
