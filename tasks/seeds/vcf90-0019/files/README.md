# vcfhosts

A Go module that exports the ESXi host inventory of a VMware Cloud Foundation 9.0
SDDC Manager.

What exists so far:

* `testdata/hosts.json` — the host inventory the loopback mock pages over.
* `verification/` — the protected checks.
* `scripts/verify.sh` — the deterministic check runner.

What has to be built:

* `docs/contract.json` — the operations pinned from the published SDDC Manager
  OpenAPI document.
* `docs/official_sources.json` — where that document was read from.
* `pkg/mock` — a loopback SDDC Manager pinned to the contract, with a request log.
* `pkg/client` — the client that sweeps the paginated host inventory.

`verification/`, `scripts/verify.sh` and `testdata/hosts.json` are protected: the
checks fail if they change. Standard library only — `scripts/verify.sh` runs with
the module proxy switched off, so a third-party dependency will not resolve.

Run `./scripts/verify.sh`. Everything it exercises is local; the mock binds to
127.0.0.1 and no VMware endpoint is contacted.
