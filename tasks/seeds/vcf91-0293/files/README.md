# vcfon-syslog — syslog forwarding rollout for VCF Operations for Networks 9.1

A dependency-free Python package that pushes an operator-authored syslog
forwarding plan to a VCF Operations for Networks 9.1 appliance (the successor to
vRealize Network Insight) and reports exactly what landed.

Rolling out syslog targets is not transactional. The appliance applies each
target on its own, so a plan can fail halfway: some targets are already live on
the appliance while the rest never got sent. The report this tool produces is the
only record an operator has of that split, so it has to be exactly right.

## Layout

| Path | Role |
| --- | --- |
| `docs/contract.json` | The REST contract, derived from the VCF Operations for Networks OpenAPI document. Authoritative for method, path, payload shape and success status. |
| `docs/official_sources.json` | Provenance of that contract: repository, commit sha, spec path, and every operationId used. |
| `src/vcfon_syslog/transport.py` | Raw HTTP. Serializes whatever mapping it is handed; never raises on 4xx/5xx. Provided. |
| `src/vcfon_syslog/plan.py` | Plan dataclasses and JSON loader. Provided. |
| `src/vcfon_syslog/errors.py` | `VcfOnApiError`, built from an ApiError response body. Provided. |
| `src/vcfon_syslog/client.py` | One method per contract operation. **To implement.** |
| `src/vcfon_syslog/rollout.py` | `apply_syslog_plan`. **To implement.** |
| `fixtures/*.json` | Example plans. |
| `tests/mock_appliance.py` | Loopback mock appliance pinned to `docs/contract.json`, with a JSONL request log. |
| `tests/verify.py` | The verifier. `python3 tests/verify.py` |

## Operations used

All six are named in `docs/contract.json` by their spec operationId:

`create`, `getSyslogTargetList`, `addSyslogTarget`, `updateSyslogTarget`,
`sendSyslogTestMessage`, `delete`.

## Run order

`apply_syslog_plan(plan, base_url, timeout=10.0)` performs, in this order:

1. `create` — exchange the plan credentials for a token. This request carries no
   `Authorization` header; the spec declares `security: []` on it. Every later
   request carries `Authorization: NetworkInsight {token}`.
2. `getSyslogTargetList` — read the targets the appliance already has. A plan
   target whose `ip_or_fqdn` appears in that list is an `update`; one that does
   not is an `add`.
3. For each plan target, **in plan order**, `addSyslogTarget` or
   `updateSyslogTarget` as decided in step 2.
   - The first target that comes back non-2xx ends this stage. Record its HTTP
     status and the `code` and `message` from its ApiError body verbatim.
   - Targets after the failing one are **not sent**. They are reported as
     skipped.
   - Targets already applied are **not rolled back**. They are live on the
     appliance and the report must say so.
4. `sendSyslogTestMessage` — once for each target that was applied, in plan
   order, with the same payload that was used to apply it. This runs whether or
   not stage 3 failed: it is how the report learns what is actually working.
   `StatusResponse.status` is a boolean and can be `false` on an HTTP 200 — that
   means the target is applied but the test log did not get through.
5. `delete` — release the token. This happens even when stage 3 failed. The
   appliance caps a user at 100 live tokens, so a run that leaks one is a bug.

## Wire rules

- Request bodies are `application/json`.
- `port` is a JSON number. Never a quoted string.
- An optional field the operator did not set is **absent from the JSON object**.
  Not `null`, not `""`, not `0`, not `{}`. A present key is an explicit
  assignment on this API, so sending `"nick_name": null` clears a nickname the
  operator never mentioned. This applies to `nick_name` and `collector_id` on a
  syslog target, to `value` inside a `Domain` (the spec notes it is not required
  for a `LOCAL` domain), and to the whole `domain` object when the plan has no
  domain.
- `collector_id` applies only to the cloud offering. Plans here are on-prem and
  never set it, so it never appears on the wire.
- On `updateSyslogTarget` the `{ip-or-fqdn}` path segment and the body's
  `ip_or_fqdn` are the same value.
- No operation outside `docs/contract.json` is ever called.

## Report shape

`apply_syslog_plan` returns a plain dict:

```
{
  "outcome": "applied" | "partial_failure",
  "existing_targets": [str, ...],   # ip_or_fqdn from getSyslogTargetList, server order
  "targets": [ <one entry per plan target, in plan order> ],
  "applied_count": int,
  "failed_index": int | None,       # 0-based index into "targets", None when nothing failed
  "token_released": bool
}
```

A target entry carries exactly the keys its status calls for — the same
omit-when-unset discipline as the wire:

| status | keys |
| --- | --- |
| `applied` | `ip_or_fqdn`, `action`, `status`, `verified` |
| `failed` | `ip_or_fqdn`, `action`, `status`, `http_status`, `error` |
| `skipped` | `ip_or_fqdn`, `action`, `status` |

`action` is `"add"` or `"update"` and is known for every target, including
skipped ones, because stage 2 ran before anything was sent. `verified` is the
boolean `status` from that target's `StatusResponse`. `error` is
`{"code": int, "message": str}` copied from the ApiError body.

`outcome` is `"partial_failure"` when any target failed, otherwise `"applied"`.

## Running

```
python3 tests/verify.py
```

The verifier starts the loopback mock on `127.0.0.1`, runs three plans through
it and checks both the returned report and the recorded request log. It contacts
no live VMware endpoint.
