# Contract and source-record formats

Two files under `docs/` describe the slice of the vSphere Automation API this
project talks to. Both are hand-maintained but every value in them is copied
from the OpenAPI document, not from prose documentation.

The mock endpoint in `mock/VcenterMock.java` reads `docs/contract.json` at
startup and exposes exactly the operations it names. An operation the contract
does not name is not routed, and a method/path pair the endpoint cannot
simulate makes startup fail rather than silently pass.

---

## `docs/contract.json`

```jsonc
{
  "source": {
    "repository": "<owner/name of the specification repository>",
    "tag": "<git tag the contract was derived from>",
    "spec_path": "<path of the OpenAPI document inside that repository>",
    "spec_version": "<the document's own info.version>"
  },

  // Path component of the server URL in the document's `servers` block, without
  // a trailing slash. Operation paths below are relative to it.
  "base_path": "<string beginning with '/'>",

  "auth": {
    // operation_id of the operation that exchanges credentials for a session
    // token, and the name of the header that carries that token on every
    // subsequent request. Both come from the document: the operation from
    // `paths`, the header name from the API-key security scheme in
    // `components.securitySchemes`.
    "session_create_operation_id": "<string>",
    "session_header": "<string>"
  },

  // One entry per operation the client invokes, in the order it invokes them.
  "operations": [
    {
      // Verbatim `operationId` from the document.
      "operation_id": "<string>",

      // Upper-case HTTP method the operation is declared under.
      "method": "GET" /* | "POST" | "PATCH" | ... */,

      // Path key from `paths`, relative to base_path, with any `{placeholder}`
      // segments left intact. Query parameters do NOT belong here: some path
      // keys in this document carry a `?action=...` suffix to disambiguate two
      // operations on the same path and method-family; split that suffix out
      // into the "query" object below.
      "path": "<string beginning with '/'>",

      // Query parameters that identify the operation, as an object of
      // name -> value. Use {} when the operation takes none.
      "query": { },

      // Names of the security schemes the operation declares, in document
      // order.
      "security": ["<scheme name>", "..."],

      // The single 2xx status code the operation declares on success.
      "success_status": 204,

      // null when the operation declares no requestBody. Otherwise:
      "request_body": {
        // Name of the component schema the requestBody's application/json
        // content refers to, e.g. "Some.Namespace.SomeSpec".
        "schema": "<string>",

        // The requestBody's own `required` flag.
        "required": true,

        // Property names of that schema, split by whether the schema's
        // `required` list names them. Both arrays are sorted lexicographically;
        // either may be empty. These drive the client's serialisation: a
        // property in "optional_properties" that the caller did not ask for
        // must be left out of the request body entirely.
        "required_properties": ["<string>", "..."],
        "optional_properties": ["<string>", "..."]
      }
    }
  ]
}
```

---

## `docs/official_sources.json`

Records where the contract came from, precisely enough that a reviewer can
re-fetch the exact bytes.

```jsonc
{
  "sources": [
    {
      "title": "<human-readable name of the document>",
      "repository": "<owner/name>",
      "tag": "<git tag>",

      // Full 40-character commit SHA that the tag resolves to. A tag can be
      // moved; the SHA cannot.
      "commit_sha": "<40 lowercase hex characters>",

      "spec_path": "<path of the OpenAPI document inside the repository>",

      // A URL that fetches this exact revision of the document. It must contain
      // the commit SHA above -- a branch- or tag-based URL is not a pin.
      "url": "<string>",

      "license": "<SPDX identifier of the repository's licence>",

      // Date the document was fetched, ISO-8601 (YYYY-MM-DD).
      "retrieved": "<string>",

      // Every operationId the contract names, sorted lexicographically.
      "operation_ids": ["<string>", "..."]
    }
  ]
}
```
