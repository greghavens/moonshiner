# Retry-safe VCF external service configuration

Implement `src/vcf_depot/client.py` as a standard-library-only Python 3.11+
client for the protected VMware Cloud Foundation 9.1 SDDC Manager contract in
`docs/contract.json`. The contract is an exact subset derived from the official
OpenAPI specification identified in `docs/official_sources.json`; do not use a
documentation page as a substitute for that contract.

The public imports are already defined:

```python
from vcf_depot import SddcManagerClient, SddcManagerError
```

`SddcManagerClient` must have this constructor:

```python
SddcManagerClient(
    base_url: str,
    access_token: str,
    *,
    timeout: float = 10.0,
    max_attempts: int = 2,
)
```

Reject an empty `base_url` or `access_token`, a non-positive `timeout`, and
`max_attempts < 1` with `ValueError`.

Implement this method:

```python
client.update_services_config(
    services: list[dict[str, object]],
) -> dict[str, object]
```

The call implements the OpenAPI operationId `updateServicesConfig`:

- send `PUT /v1/services-config` with no query string;
- send `Authorization: Bearer <access_token>`, `Accept: application/json`, and
  `Content-Type: application/json`;
- require a nonempty list of service objects and encode it as a
  `ServicesConfig` body whose only top-level member is `services`;
- accept only the contract's `200` response with a JSON object containing a
  `services` list and return that decoded object.

This `PUT` replaces desired external-service state, so an ambiguous failure is
safe to retry.
Serialize the request once, then replay the same method, URL, headers, and body
bytes after HTTP `500`, `502`, `503`, or `504`, or a connection-level
`urllib.error.URLError`, up to `max_attempts` total sends. Do not retry other
HTTP statuses. Raise `SddcManagerError` after a non-retryable response, after
the attempt limit, for a malformed success body, or for any other protocol
failure. When an HTTP response caused the error, expose its integer status as
the exception's `status` attribute; otherwise `status` is `None`.

Only `src/vcf_depot/client.py` is editable; do not modify `tests/` or
`docs/`, and do not add dependencies. Run `python3 -B tests/verify.py` to
verify. Verification starts the bundled mock on `127.0.0.1` with an ephemeral
port. The mock applies
the first valid mutation and deliberately returns a transient error before
acknowledging an identical retry, allowing the verifier to prove that retrying
did not duplicate the effect. No live VMware system is used.
