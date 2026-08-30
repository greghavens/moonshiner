# VCF 9.1 NSX Policy client exercise

The integration is deliberately small: `src/NsxPolicyClient.java` is the only
production source file and uses only the Java 17 standard library.

The protected contract is a focused extraction from the official VCF 9.1 NSX
Policy OpenAPI 2.0 specification. `docs/official_sources.json` pins the
repository commit, specification path, and the two operation IDs used here.
The loopback service loads those operations from `docs/contract.json` and has
no route for any other NSX operation. It never contacts a VMware endpoint.

Complete the client so that:

- `patchGroup` sends the contract's `PatchGroupForDomain` request.
- `readGroup` sends the contract's `ReadGroupForDomain` request.
- every request starts with `AccessTokenProvider.currentToken()`;
  after a `401`, refresh exactly once and retry only that failed HTTP request;
  do not replay an earlier successful write.
- non-2xx responses after the one allowed refresh are reported as
  `IOException`.
- JSON is UTF-8, correctly escaped, and contains no properties for unset
  optional values. In particular, null `description`, `groupType`, and
  `scopeOperator` values must not become empty strings, empty arrays, or JSON
  `null`.

Run `./verify.sh` from the repository root. The verifier compiles with
`javac --release 17` and runs `TestMain`.
