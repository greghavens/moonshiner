# vcfops-runbook

Change-automation for our VMware Cloud Foundation 9.1 estate. This repository
holds the tooling that applies scheduled maintenance change requests to **VCF
Operations** through its `suite-api` REST API.

Everything here is stdlib-only Python 3. We deploy this onto the jump hosts as a
plain directory drop, so no third-party packages and no build step.

## Layout

| Path | What it is |
| --- | --- |
| `runbook/change_request.json` | The change request currently queued for the Payments platform. |
| `docs/` | Where the derived API contract and its provenance record live. |
| `tools/vcfops_mock.py` | Loopback stand-in for a VCF Operations appliance, for offline work. |
| `tests/` | The repository's checks. |
| `scripts/verify.sh` | Runs the checks. |

## The appliance stand-in

`tools/vcfops_mock.py` speaks `suite-api` on `127.0.0.1` so nobody has to point
development traffic at a real appliance. It is **pinned to `docs/contract.json`**:
at startup it reads that file, reduces it to a route projection, and refuses to
start unless the projection digest matches the appliance build it represents. It
then routes only the operations the contract names — anything else answers 404.

The projection and digest are computed as:

```python
rows = sorted([[op["operationId"], op["method"].upper(), op["path"]]
               for op in contract["operations"]])
digest = hashlib.sha256(
    json.dumps(rows, separators=(",", ":")).encode("utf-8")
).hexdigest()
```

If the digest does not match, the process exits `3` and prints both digests plus
the routes it loaded. That is the fast way to find out whether the contract has
drifted from the appliance.

Run it directly:

```sh
python3 tools/vcfops_mock.py --contract docs/contract.json --log /tmp/requests.ndjson
```

It prints one JSON line, `{"port": 54321}`, and then serves. Every request it
receives is appended to the NDJSON log as one object per line with `seq`,
`method`, `path`, `query`, `headers`, `body_raw`, `body`, `operationId` and
`status`.

Credentials on the stand-in: local user `svc-runbook`, password `Runb00k!Ops`.

## Checks

```sh
bash scripts/verify.sh
```
