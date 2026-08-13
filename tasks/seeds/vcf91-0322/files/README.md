# vcfa — a VCF Automation deployment client

A dependency-free Python client for the deployment APIs of **VCF Automation** in
VMware Cloud Foundation 9.1, the successor to vRealize / Aria Automation. The Python
standard library is the only dependency.

## Layout

| Path | What it is |
| --- | --- |
| `docs/contract.json` | The request contract this client must satisfy. |
| `docs/official_sources.json` | The reference pages the contract was derived from, with fetch dates. |
| `vcfa/transport.py` | A thin HTTP transport over `urllib`. Sends exactly what it is handed. |
| `vcfa/client.py` | One method per contract operation. |
| `vcfa/diagnose.py` | The `python -m vcfa.diagnose` triage entry point. |
| `vcfa_mock/` | A loopback stand-in for a VCF Automation appliance, pinned to the contract. |
| `tests/` | The verifier. |

## The contract is reference-derived

VCF Automation publishes no machine-readable API specification — there is nothing for it
in the `vmware/vcf-api-specs` repository. `docs/contract.json` was therefore derived by
reading the VCF Automation xAPIs reference documentation on the Broadcom Developer
Portal, and it says so in its own `source` block. Treat it as the authority for this
codebase, and treat `docs/official_sources.json` as the audit trail back to the pages it
came from.

Two rules in `conventions` are easy to violate by accident and are checked:

* an optional query parameter or optional body field that is not in use must be **omitted
  entirely**, never sent empty, null, `{}` or `[]`;
* a request must not carry a parameter or field the contract does not declare for that
  operation.

## The mock

`vcfa_mock` builds its routing table from `docs/contract.json` and serves only the
operations the contract names. Anything else gets a 404, and an undeclared query
parameter or body field gets a 400. It binds to `127.0.0.1` and contacts nothing.

Start one by hand to poke at it:

```console
$ python3 -m vcfa_mock.server --log /tmp/vcfa-requests.jsonl
{"base_url": "http://127.0.0.1:41225", "tenant": "payments", "api_token": "...", "log": "/tmp/vcfa-requests.jsonl"}
```

Every request it receives is appended to the `--log` file as one JSON object per line,
recording the method, path, raw and parsed query string, headers and body. Reading that
log is the quickest way to see what your client actually put on the wire.

## Running the client

```console
$ python3 -m vcfa.diagnose \
    --base-url http://127.0.0.1:41225 \
    --tenant payments \
    --api-token vcfa-api-token-7Qx2LmR9tKpZ \
    --deployment payments-uat-07 \
    --out diagnosis.json
```

## Verifying

```console
$ python3 -m unittest tests.verify_wire_contract -v
```

The verifier starts its own mock on a free port, runs `vcfa.diagnose` against it, and
checks both the report and the recorded traffic. It contacts no VMware endpoint.
