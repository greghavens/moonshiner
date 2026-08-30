# vcfauto

A Go client for a slice of the VMware Cloud Foundation 9.1 **VCF Automation**
API — the successor to vRealize and Aria Automation — together with a loopback
mock and a wire-shape verifier for testing against it.

## Layout

| path | state |
| --- | --- |
| `contract/` | **complete.** Types and a strict loader for `docs/contract.json` and `docs/official_sources.json`. |
| `docs/` | **empty.** `contract.json` and `official_sources.json` go here. See `docs/README.md`. |
| `automation/` | **stubs.** The client. `opt.go` is complete; `client.go` declares the API and returns `errNotImplemented`. |
| `mock/` | **stubs.** The loopback mock. `fixtures.go` is complete; `server.go` declares the API. |
| `wire/` | **stubs.** The request-shape verifier. |
| `verification/` | **protected.** The acceptance suite. It is restored before grading, so editing it achieves nothing. |

Exported signatures in `automation`, `mock` and `wire` are fixed — the
acceptance suite compiles against them. Everything unexported is yours.

## Why there is a contract file at all

VCF Automation ships no machine-readable specification: it is absent from the
`vmware/vcf-api-specs` repository, and there is no OpenAPI document to generate
from or validate against. What exists is reference documentation — human-facing
pages, one per operation. So the contract is transcribed by hand, and
`docs/official_sources.json` records where each line of it came from and when,
because a hand transcription with no provenance is not worth much six months
later.

Both the client and the mock are driven from the loaded contract rather than
from string literals, so that the two cannot drift apart independently: if the
contract is wrong they are wrong together and visibly, rather than one of them
being quietly wrong in production.

## Running the tests

```sh
go test -race ./...
```

The suite must pass under `-race`. Nothing in it touches the network: every
request goes to a mock bound to `127.0.0.1`.
