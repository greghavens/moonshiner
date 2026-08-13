# vcfops

Go integration library for the VMware Cloud Foundation Operations API (VCF 9.1).

## Layout

| Path | Purpose |
| --- | --- |
| `opsadapter/` | The client package. Registers adapter instances against VCF Operations. |
| `internal/opsmock/` | In-memory HTTP double for the VCF Operations API, pinned to `docs/contract.json`. Test support only. |
| `docs/` | The wire contract derived from the published OpenAPI specification, plus source provenance. |
| `verification/` | Acceptance checks. |

## Running the checks

```sh
bash verification/run_verification.sh
```

The suite runs entirely offline against `internal/opsmock`; it never contacts a
live VCF Operations deployment.
