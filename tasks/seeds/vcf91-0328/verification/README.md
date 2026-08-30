# vcfauto

A Go module for driving gated day-2 actions against VCF Automation deployments
in VMware Cloud Foundation 9.1.

## Layout

| Path                        | What it is                                                        |
| --------------------------- | ----------------------------------------------------------------- |
| `client.go`                 | package `vcfauto` — the API client                                 |
| `mockapi/`                  | package `mockapi` — loopback stand-in pinned to `docs/contract.json` |
| `docs/contract.json`        | the REST contract, derived from reference documentation            |
| `docs/official_sources.json`| every documentation page the contract was derived from             |
| `verify/`                   | protected acceptance checks — do not edit                          |

## Running the checks

```
go test -race ./...
bash verify/run.sh
```

Everything runs offline. The tests talk to `mockapi` over loopback; no live
VMware endpoint is contacted.
