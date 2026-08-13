# vcf-restore-drill

A restore drill for VMware Cloud Foundation 9.1. It reads the SDDC LCM
inventory, picks a backup for each planned component, restores the components
one at a time in plan order, polls every restore task to a terminal state and
writes a report of what actually happened.

## Layout

| Path                       | What it is                                                    |
| -------------------------- | ------------------------------------------------------------- |
| `docs/contract.json`       | the REST contract derived from the SDDC LCM OpenAPI spec       |
| `docs/official_sources.json` | where that contract was read from                            |
| `drill/`                   | the drill package                                              |
| `cmd/vcf-restore-drill/`   | the command line entry point                                   |
| `fixtures/restore-plan.json` | an example plan                                              |
| `internal/mocklcm/`        | the loopback mock of the SDDC LCM service                      |
| `tools/mockserve/`         | runs that mock so the drill can be driven by hand              |
| `contracttest/`            | the contract test                                              |

`fixtures/`, `internal/`, `tools/`, `contracttest/`, `go.mod`, `README.md` and
`verify.sh` are fixed inputs. Do not edit them.

## Checking your work

```
bash verify.sh
```

builds the module and runs `go test -race ./contracttest/...`. The test starts
the mock on loopback, drives the drill against it and inspects every request the
drill made. No VMware endpoint is contacted.

## Driving it by hand

The mock builds its routes from `docs/contract.json`, so it only runs once that
file describes the operations correctly.

```
go run ./tools/mockserve -contract docs/contract.json -token demo-token
```

It prints the loopback URL it is listening on. In another shell:

```
go run ./cmd/vcf-restore-drill \
    -plan fixtures/restore-plan.json \
    -base-url http://127.0.0.1:PORT \
    -token demo-token \
    -report /tmp/report.json \
    -poll-interval 200ms
```

Stop the mock with Ctrl-C and it prints every request it received, one JSON
object per line, along with any request that did not match the contract.

`mockserve` takes `-polls N` to control how many polls a task answers before it
settles, and `-fail COMPONENTTYPE` to make one component's restore task fail.

The mock's sample fleet is `vidb`, `opscp` and `vcfops` at FLEET scope and
`vcfa` at INSTANCE scope.
