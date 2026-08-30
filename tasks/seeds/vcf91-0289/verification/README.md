# vcfonw-certrotate

Replaces the appliance certificate on a VMware Cloud Foundation 9.1 **VCF Operations
for Networks** deployment (the successor to vRealize Network Insight) and follows the
resulting update to a terminal state.

Standard library only — no third-party packages, Python 3.10+.

## Layout

| Path | What it is |
| --- | --- |
| `vcfonw_certrotate/client.py` | The rotation flow. **This is the file to write.** |
| `vcfonw_certrotate/model.py` | `RotationOutcome`, `ApiError`, `PollTimeoutError` — fixed shapes. |
| `docs/contract.json` | The REST surface, derived from the pinned OpenAPI revision. |
| `docs/official_sources.json` | Repository, commit sha and operationIds the contract came from. |
| `tests/mock_vcfonw_server.py` | Loopback appliance mock, routed from `docs/contract.json`. |
| `tests/run_case.py` | Runs one rotation against the mock and prints the result. |
| `tests/verify.py` | Checks the wire shape recorded in the mock's request log. |

## Contract

`docs/contract.json` is derived from
`specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml` at commit
`c3f3b52c845dd967cabbc21680e893292077d5ba` of
[vmware/vcf-api-specs](https://github.com/vmware/vcf-api-specs) (Apache-2.0). It names
four operations and nothing else may be called:

| Role | operationId | Request |
| --- | --- | --- |
| `authenticate` | `create` | `POST /api/ni/auth/token` |
| `submit_certificate_update` | `updateCertificate` | `PUT /api/ni/settings/certificates/{id}` |
| `poll_update_status` | `fetchCertificateUpdateStatusForUpdateId` | `GET /api/ni/settings/certificates/status/{id}` |
| `revoke_token` | `delete` | `DELETE /api/ni/auth/token` |

## Running the checks

```
python3 tests/verify.py
```

Everything runs against `127.0.0.1`; no appliance is contacted. The mock builds its
routes from `docs/contract.json` and answers any other path with 404, and it appends
every request it receives to a JSON Lines log that `tests/verify.py` reads back.
