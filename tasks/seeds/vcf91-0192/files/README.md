# VCF Operations Log Management retry fixture

This fixture exercises a single Java 17 source file against a loopback-only HTTP
mock. No external dependency, build tool download, or live VMware service is
used.

The REST projection in `docs/contract.json` was transcribed from VMware's
Apache-2.0 OpenAPI specification at the immutable repository revision recorded
in `docs/official_sources.json`. It contains exactly one operation:
`updateLogForwarder`.

Run the protected acceptance check from this directory:

```sh
python3 -B tests/verify.py
```

The verifier compiles into a temporary directory, launches the mock on
`127.0.0.1` with an ephemeral port, runs `TestMain`, and reads the mock's JSONL
request log. The first valid PUT is applied but receives a contract-listed 500;
the repeated identical PUT receives 200. The mock counts a changed resource
representation as an effect, so the verifier can distinguish two requests from
two effects.
