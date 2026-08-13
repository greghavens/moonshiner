# vcfon-vcenter — precheck-gated vCenter onboarding for VCF Operations for Networks 9.1

A dependency-free Python package that registers a batch of vCenter data sources
with a VCF Operations for Networks 9.1 appliance (the successor to vRealize
Network Insight), running each vCenter's precheck before anything is created.

Registering a data source is a mutation: the appliance starts collecting, the
collector VM takes on load, and an operator has to go and delete a bad entry by
hand. The appliance therefore exposes a precheck for exactly this, and the rule
this tool exists to enforce is simple: **if the precheck does not pass, nothing
is created for that vCenter.**

## Layout

| Path | Role |
| --- | --- |
| `docs/contract.json` | The REST contract, derived from the VCF Operations for Networks OpenAPI document. Authoritative for method, path, payload shape and success status. |
| `docs/official_sources.json` | Provenance of that contract: repository, commit sha, spec path, and every operationId used. |
| `src/vcfon_vcenter/transport.py` | Raw HTTP. Serializes whatever mapping it is handed; never raises on 4xx/5xx. Provided. |
| `src/vcfon_vcenter/plan.py` | Plan dataclasses and JSON loader. Provided. |
| `src/vcfon_vcenter/errors.py` | `VcfOnApiError`, built from an ApiError response body. Provided. |
| `src/vcfon_vcenter/client.py` | Three body builders and one method per contract operation. **To implement.** |
| `src/vcfon_vcenter/onboarding.py` | `onboard_vcenters`. **To implement.** |
| `fixtures/*.json` | Example plans. |
| `tests/mock_appliance.py` | Loopback mock appliance pinned to `docs/contract.json`, with a JSONL request log. |
| `tests/verify.py` | The verifier. `python3 tests/verify.py` |

## Operations used

All four are named in `docs/contract.json` by their spec operationId:

`create`, `validateVCenter`, `addVcenterDatasource`, `delete`.

Nothing outside that set is ever called.

## Run order

`onboard_vcenters(plan, base_url, timeout=10.0)` performs, in this order:

1. `create` — exchange the plan credentials for a token. This request carries no
   `Authorization` header; the spec declares `security: []` on it. Every later
   request carries `Authorization: NetworkInsight {token}`.
2. For each plan datasource, **in plan order**:
   1. `validateVCenter` for that vCenter.
   2. `addVcenterDatasource` for that same vCenter — **only if** the precheck
      passed. See the gate below.
   One vCenter's outcome never stops the next one: the batch runs to the end.
3. `delete` — release the token, whatever happened above. The appliance caps a
   user at 100 live tokens, so a run that leaks one is a bug.

## The precheck gate

The precheck passes only when `validateVCenter` answered **HTTP 200** *and* the
`BaseDataSourceValidationResponse` body's own `code` is **200**.

`BaseDataSourceValidationResponse` is `{code, message}`. The appliance answers
HTTP 200 with a body `code` of, say, `401` when the request was well formed but
the verdict is negative — wrong service-account password, rejected certificate,
unreachable host. The HTTP status is about the request; the body `code` is the
verdict. Gating on the HTTP status alone registers a data source the appliance
just told you not to register.

A non-200 HTTP status from `validateVCenter` is also a failed precheck. Either
way, `addVcenterDatasource` for that vCenter is **not sent at all** — not sent
and ignored, not sent.

## Wire rules

- Request bodies are `application/json`.
- Exactly one of `ip` and `fqdn` is sent, never both, never neither. The plan
  loader already guarantees the spec has one of them; the one that is unset must
  be **absent** from the body.
- An optional field the operator did not set is **absent from the JSON object**.
  Not `null`, not `""`, not `0`, not `{}`. A present key is an explicit
  assignment on this API. This covers `notes`, `enabled`, `is_vmc` and
  `ipfix_request` on a create; `ipfix_enabled` on a validate; `value` inside a
  `Domain`; and the whole `domain` object when the plan has no domain.
- `enabled` that the operator set to `false` is **set**, not unset. It must
  appear on the wire as `"enabled": false`. Its spec default is `true`, so
  dropping it silently flips the operator's intent.
- Inside `ipfix_request`, only the keys the operator actually set appear.
- `VCenterDataSourceValidationRequest` is a **strictly smaller schema** than
  `VCenterDataSourceRequest`. Its only members are `ip`, `fqdn`, `proxy_id`,
  `credentials` and `ipfix_enabled`. `nickname`, `notes`, `enabled`, `is_vmc`,
  `ds_sub_type`, `tags`, `enable_ds_associated_tags`, `ipfix_request` and
  `antrea_ipfix_request` are **not** members of it. Sending the create body to
  the validate operation is a contract violation even though most of it
  overlaps.
- The IPFIX intent is spelled differently by the two operations: `validateVCenter`
  takes the flat boolean `ipfix_enabled`, `addVcenterDatasource` takes the
  `ipfix_request` object. A plan datasource that carries an `ipfix` section means
  `"ipfix_enabled": true` on the validate; one that does not carries neither key.
- `delete` sends no request body.

## Report shape

`onboard_vcenters` returns a plain dict:

```
{
  "outcome": "onboarded" | "partial" | "blocked",
  "datasources": [ <one entry per plan datasource, in plan order> ],
  "created_count": int,
  "blocked_count": int,   # precheck_failed entries
  "failed_count": int,    # create_failed entries
  "token_released": bool
}
```

`outcome` is `"onboarded"` when every datasource was created, `"blocked"` when
none was, and `"partial"` otherwise.

A datasource entry carries exactly the keys its status calls for — the same
omit-when-unset discipline as the wire:

| status | keys |
| --- | --- |
| `created` | `nickname`, `host`, `status`, `precheck`, `entity_id` |
| `precheck_failed` | `nickname`, `host`, `status`, `precheck` |
| `create_failed` | `nickname`, `host`, `status`, `precheck`, `error` |

- `host` is the `ip` when the spec set one, otherwise the `fqdn`.
- `precheck` is always `{"http_status": int, "code": int, "message": str}`. On an
  HTTP 200 the `code` and `message` come from the
  `BaseDataSourceValidationResponse` body. On any other status they come from the
  `ApiError` body, via `VcfOnApiError`.
- `error` has the same three keys and describes the failed
  `addVcenterDatasource` response.
- `entity_id` is the `entity_id` of the created `VCenterDataSource`.

## Running

```
python3 tests/verify.py
```

The verifier starts the loopback mock on `127.0.0.1`, runs three plans through
it, and checks both the returned report and the recorded request log. The mock
builds its routes and its accepted property sets out of `docs/contract.json`, so
it serves only the four contract operations and rejects any property that belongs
to a neighbouring schema. It contacts no live VMware endpoint.
