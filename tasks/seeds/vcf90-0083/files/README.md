# VCF Operations for Logs 9.0 module fixture

This fixture supplies the spec-derived contract and a loopback-only service for
the `GET_events-+path` operation. The mock binds to `127.0.0.1`, writes every
request to an NDJSON request log, and reads its event collection from
`mock/events.json`.

The implementation belongs in:

```text
src/Vcf.OperationsForLogs/Vcf.OperationsForLogs.psd1
src/Vcf.OperationsForLogs/Vcf.OperationsForLogs.psm1
```

Run the deterministic verifier with:

```sh
python3 -B tests/verify.py
```

The verifier starts and stops the loopback service itself. It never contacts a
VMware endpoint.
