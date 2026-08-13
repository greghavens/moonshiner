# vcfsddc

A small Go module for talking to the VMware Cloud Foundation 9.0 SDDC Manager
REST API, together with a loopback mock of that API so the client can be
exercised offline.

## Layout

    docs/                    the API contract this module is pinned to
    pkg/client/              the SDDC Manager client
    pkg/mock/                the loopback mock, pinned to docs/contract.json
    testdata/credentials.json  the credential inventory the mock serves
    verification/            protected checks — do not modify
    scripts/verify.sh        runs the protected checks

## Running the checks

    ./scripts/verify.sh

The script runs `go vet` and `go test -race` over the whole module with the
module proxy switched off. Everything it exercises is local: the mock binds to
127.0.0.1 and no VMware endpoint is contacted.

## Fixture

`testdata/credentials.json` is a JSON array of `Credential` objects shaped as
they appear in the `elements` array of a `PageOfCredential` response. The mock
pages and filters over this array; it is the only inventory the mock knows
about.
