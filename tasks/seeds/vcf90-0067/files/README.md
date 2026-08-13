# vcfops-triage

A small, standard-library-only Python client that annotates **VMware Cloud Foundation
Operations** (VCF 9.0) alerts with a triage note, so a run book can stamp a batch of
alerts without anyone clicking through the UI.

The awkward part is the session. `acquireToken` hands back a token with a stated
`validity`, but the appliance can decide the token is finished before then — a restart,
an idle timeout, an administrator releasing sessions. When that happens the very next
authenticated call answers `401`, in the middle of a batch that is already half done.
Re-authenticating is easy; re-authenticating **without dropping or repeating the work
already accepted** is the thing this client has to get right.

Everything the client needs to know about the wire format lives in
[`docs/contract.json`](docs/contract.json), which was derived from the VCF Operations
OpenAPI specification at tag `9.0.0.0`. Provenance is in
[`docs/official_sources.json`](docs/official_sources.json). Read the contract rather than
guessing: it names the four operations, the base path, the query parameters and their
defaults, which body fields are required, which are optional, and the exact enum
vocabulary for `alertCriticality`.

## Layout

```
docs/contract.json          the pinned 9.0.0.0 contract (do not edit)
docs/official_sources.json  where that contract came from (do not edit)
src/vcfops_triage/          the client package — your work goes here
tools/mock_ops_server.py    loopback mock pinned to the contract, with a request log
tests/verify_contract.py    the acceptance test
run_verification.sh         runs the acceptance test
```

## The command line

The package is driven through `python -m vcfops_triage`:

```
python -m vcfops_triage \
    --base-url http://127.0.0.1:8443 \
    --username admin \
    --password 'VMware1!VMware1!' \
    [--auth-source 'Local Users'] \
    --criticality CRITICAL,IMMEDIATE \
    [--alert-name 'CPU contention'] \
    --note 'Triaged by run book RB-114' \
    --output /tmp/triage-report.json \
    [--page-size 2] \
    [--max-refreshes 3]
```

`--base-url` is the scheme, host and port only; the client appends the contract's
`basePath` and operation paths. `--criticality` is a comma-separated list.
`--max-refreshes` bounds how many times the whole run may re-acquire a token
(default `3`).

### Exit codes

| Code | Meaning |
| ---- | ------- |
| `0`  | Every matched alert was annotated and the report was written to `--output`. |
| `2`  | An argument is not valid under the contract. Nothing is sent. |
| `5`  | Authentication failed, or `--max-refreshes` was used up and the token is still rejected. |
| `1`  | Anything else (transport error, unexpected status, ...). |

Diagnostics go to stderr.

### The report

`--output` receives a JSON object:

```json
{
  "alertsMatched": 5,
  "pagesFetched": 3,
  "tokenRefreshes": 1,
  "annotated": [{"alertId": "...", "noteId": "..."}]
}
```

`annotated` is in the order the alerts came back from the server, one entry per alert,
each carrying the `id` of the `alert-note` the server created.

## What the client must get right

1. **Work in page order, and annotate a page before fetching the next one.** Call
   `queryAlert` with `page` starting at `0`, note every alert on that page with
   `addAlertNote`, then move to the next page. A page that comes back with fewer entries
   than the `pageSize` actually in effect is the last one — see `paging` in the
   contract — so do not request the page after it.

2. **Refresh on `401`, then replay exactly one request.** A `401` from any authenticated
   operation means the token is finished. Acquire a new one and re-send **the single
   request that was rejected**, with the new token. Do not start the batch again, do not
   re-fetch a page you already have, and do not re-note an alert whose note the server
   already accepted. Count each re-acquisition against `--max-refreshes`; when that
   budget is used up and the request is still rejected, exit `5` — and do not call
   `releaseToken`, because there is no live token to release. On every other path,
   release the token you are holding when you are done.

3. **Omit optional fields you were not given.** If `--auth-source` is absent, the
   `acquireToken` body carries `username` and `password` and nothing else — not
   `"authSource": null` and not `"authSource": ""`. If `--alert-name` is absent, the
   `alert-query` body carries `activeOnly` and `alertCriticality` and nothing else — an
   omitted criterion means "do not filter on this", so there is no such thing as an
   empty one. If `--page-size` is absent, the `pageSize` query parameter is not on the
   URL at all and the server's documented default of `1000` applies; `page` is always
   sent explicitly. The `alert-note-content` body carries `content` and nothing else —
   the alert id belongs in the path, not the body.

4. **Query the way the contract says.** `activeOnly` is always `true`: this tool triages
   live alerts. Every `--criticality` value must appear in the contract's
   `alertCriticality` enum, and `--page-size` must respect the contract's minimum; if
   either is wrong, exit `2` and make no network call at all.

5. **Authenticate as the contract says.** `acquireToken` is the only operation that
   carries no `Authorization` header — the specification sets `"security": []` on it.
   The other three send `Authorization` built from the contract's `headerValueTemplate`.

## Trying it by hand

```sh
python tools/mock_ops_server.py --port 8443 --scenario expire_before_page \
    --log /tmp/requests.jsonl &
python -m vcfops_triage --base-url http://127.0.0.1:8443 --username admin \
    --password 'VMware1!VMware1!' --criticality CRITICAL,IMMEDIATE \
    --note 'Triaged by run book RB-114' --page-size 2 --output /tmp/triage-report.json
cat /tmp/requests.jsonl | python -m json.tool --json-lines
```

The mock serves only the four operations in the contract, enforces the field rules above,
and records every request. It listens on loopback and reaches no VMware service. Its
scenarios (`--scenario stable|expire_before_page|expire_before_note|always_expired`)
retire a token after a fixed number of authenticated requests rather than after a
wall-clock interval, so where the `401` lands does not depend on timing.

`PYTHONPATH=src` is needed to import the package from a source checkout;
`run_verification.sh` sets it for you.

## Verification

```sh
./run_verification.sh
```
