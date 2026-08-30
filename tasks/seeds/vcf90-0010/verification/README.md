# vcf-credrotate

Scheduled ESXi credential rotation against **VMware Cloud Foundation 9.0 SDDC
Manager**. Standard library only — no third-party packages, no network access
beyond the SDDC Manager base URL it is pointed at.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The wire contract, derived from the VCF 9.0.0.0 SDDC Manager OpenAPI specification. Authoritative. |
| `docs/official_sources.json` | Provenance: spec path, repository tag, commit sha and the operationIds used. |
| `mock/sddc_manager_mock.py` | Loopback SDDC Manager mock, pinned to `docs/contract.json`. Writes a JSONL request log. |
| `mock/fixtures.json` | The credential inventory the mock serves. |
| `tests/test_rotation_contract.py` | Verifier. Runs the client against the mock and checks the exact request wire shape. |
| `vcf_credrotate/` | The rotation client. |

## The contract

`docs/contract.json` names five operations and the mock serves only those five:

| operationId | Method | Path |
| --- | --- | --- |
| `createToken` | POST | `/v1/tokens` |
| `refreshAccessToken` | PATCH | `/v1/tokens/access-token/refresh` |
| `getCredentials` | GET | `/v1/credentials` |
| `updateOrRotatePasswords` | PATCH | `/v1/credentials` |
| `getCredentialsTask` | GET | `/v1/credentials/tasks/{id}` |

Read the contract rather than guessing from the endpoint names — several of
these shapes are not what you would assume, and `docs/contract.json` has a
`notes` array calling out the ones that bite.

## Wire rules

* Authorized calls carry `Authorization: Bearer <accessToken>`.
* The API never advertises the access token lifetime. A `401` is the only
  expiry signal, and it can arrive on any authorized call.
* **Unset optional fields are omitted.** A field you do not have a value for is
  left out of the JSON body entirely — never sent as `null` or `""`. The same
  applies to query parameters: an unset filter does not appear in the query
  string at all. The appliance rejects requests that violate this with `400`.

## `rotate_credentials`

```python
rotate_credentials(base_url, username, password,
                   resource_type="ESXI", page_size=100, poll_interval=5.0) -> dict
```

Returns:

```python
{
    "rotated_credential_ids": [...],  # sorted credential ids covered by the task
    "task_id": "cred-task-0001",
    "task_status": "SUCCESSFUL",
    "token_refreshes": 2,             # successful refreshAccessToken calls
}
```

## Running things

```sh
python3 tests/test_rotation_contract.py          # the verifier
python3 mock/sddc_manager_mock.py --port 8931    # the mock on its own
```
