# vcfops-export

A small, standard-library-only Python client that exports content from **VMware Cloud
Foundation Operations** (VCF 9.0) and saves the resulting archive to disk.

Content export is **asynchronous**. `exportContent` answers `202 Accepted` — that means the
export has been queued, not that it has finished. The archive only exists once the operation
reaches a terminal state, and asking for it early fails.

Everything the client needs to know about the wire format lives in
[`docs/contract.json`](docs/contract.json), which was derived from the VCF Operations OpenAPI
specification at tag `9.0.0.0`. Provenance is in
[`docs/official_sources.json`](docs/official_sources.json). Read the contract rather than
guessing: it names the five operations, the base path, which body fields are required, which
are optional, and the exact enum vocabularies for `scope`, `contentTypes` and the operation
`state`.

## Layout

```
docs/contract.json          the pinned 9.0.0.0 contract (do not edit)
docs/official_sources.json  where that contract came from (do not edit)
src/vcfops_export/          the client package — your work goes here
tools/mock_ops_server.py    loopback mock pinned to the contract, with a request log
tests/verify_contract.py    the acceptance test
run_verification.sh         runs the acceptance test
```

## The command line

The package is driven through `python -m vcfops_export`:

```
python -m vcfops_export \
    --base-url http://127.0.0.1:8443 \
    --username admin \
    --password 'VMware1!VMware1!' \
    [--auth-source 'Local Users'] \
    --scope CUSTOM \
    --content-types POLICIES,DASHBOARDS,ALERT_DEFINITIONS \
    [--encryption-password 's3cret'] \
    --output /tmp/export.zip \
    [--poll-interval 2.0] \
    [--max-polls 60]
```

`--base-url` is the scheme, host and port only; the client appends the contract's `basePath`
and operation paths. `--content-types` is a comma-separated list. `--poll-interval` is seconds
between polls (default `2.0`); `--max-polls` is how many polls to make before giving up
(default `60`).

### Exit codes

| Code | Meaning |
| ---- | ------- |
| `0`  | The export reached `FINISHED` and the archive was written to `--output`. |
| `2`  | `--scope` or `--content-types` is not valid under the contract. Nothing is sent. |
| `3`  | The export reached the terminal failure state. No archive is written. |
| `4`  | `--max-polls` was exhausted without reaching a terminal state. No archive is written. |
| `5`  | Authentication failed. |
| `1`  | Anything else (transport error, unexpected status, ...). |

Diagnostics go to stderr; on failure include the operation state and any `errorMessages`.

## What the client must get right

1. **Poll to a terminal state.** After `202 Accepted`, poll `getLastExportOperation` and read
   its `state`. Only `FINISHED` and `FAILED` end the loop — see `polling` in the contract. Call
   `download` only after a poll has actually reported `FINISHED`; never straight after the
   `202`, and never after `FAILED`.

2. **Omit optional fields you were not given.** If `--auth-source` is absent, the
   `acquireToken` body carries `username` and `password` and nothing else — not
   `"authSource": null` and not `"authSource": ""`. Likewise, without
   `--encryption-password` the `EncryptionPassword` header is not sent at all, rather than
   sent empty, and the `content-export` body carries only `scope` and `contentTypes`.

3. **Validate against the contract before sending.** `--scope` and every `--content-types`
   value must appear in the contract's enums. If one does not, exit `2` and make no network
   call at all. Note that the 9.1 revision of this specification added content types that do
   not exist in 9.0; the pinned contract is the authority here.

4. **Authenticate as the contract says.** `acquireToken` is the only operation that carries no
   `Authorization` header — the specification sets `"security": []` on it. The other four send
   `Authorization` built from the contract's `headerValueTemplate`. Release the token when you
   are done, on the failure paths as well as the success path.

## Trying it by hand

```sh
python tools/mock_ops_server.py --port 8443 --log /tmp/requests.jsonl &
python -m vcfops_export --base-url http://127.0.0.1:8443 --username admin \
    --password 'VMware1!VMware1!' --scope CUSTOM --content-types POLICIES \
    --output /tmp/export.zip --poll-interval 0.1
cat /tmp/requests.jsonl | python -m json.tool --json-lines
```

The mock serves only the five operations in the contract, enforces the field rules above, and
records every request. It listens on loopback and reaches no VMware service. Its scenarios
(`--scenario success|failed|stuck|immediate`) advance the operation state by *poll count*, so
runs do not depend on timing.

`PYTHONPATH=src` is needed to import the package from a source checkout; `run_verification.sh`
sets it for you.

## Verification

```sh
./run_verification.sh
```
