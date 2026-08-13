# vcf-fleet-run

An internal tool that installs VMware Cloud Foundation 9.1 fleet components
through the SDDC LCM service and reports what happened.

A run is long: resolving binaries from the Fleet depot and deploying appliances
takes far longer than an access token lives. The token therefore expires part
way through, and the run has to carry on from where it was rather than start
again — re-raising an install that the service already accepted would deploy
twice.

## Layout

    docs/contract.json           the REST contract, derived from the OpenAPI specification
    docs/official_sources.json   where that specification was read, pinned to a commit
    fleetrun/                    the run
    cmd/vcf-fleet-run/           the command line tool
    fixtures/install-plan.json   an example plan
    internal/mocklcm/            loopback mock of the SDDC LCM service
    tools/mockserve/             runs that mock so the tool can be driven by hand
    contracttest/                the contract test

`go.mod`, `README.md`, `verify.sh`, `fixtures/`, `internal/`, `tools/` and
`contracttest/` are fixed inputs.

## Checking your work

    bash verify.sh

This builds the module, vets it and runs `go test -race ./contracttest/...`. The
test starts the mock on loopback, drives the run against it and inspects every
recorded request: the target, the method, the credential, the headers and the
exact JSON body. No VMware endpoint is contacted.

## Driving the mock by hand

    go run ./tools/mockserve -scenario retry -tokens tok-a,tok-b -token-uses 3

It prints the address it is listening on. Point the tool at it:

    printf 'tok-a\ntok-b\n' > /tmp/tokens
    go run ./cmd/vcf-fleet-run \
        -plan fixtures/install-plan.json \
        -base-url http://127.0.0.1:PORT \
        -token-file /tmp/tokens \
        -report /tmp/report.json \
        -poll-interval 200ms

`-scenario` is `succeed`, `retry` or `fail`. `-token-uses` is how many requests
each token serves before the mock starts rejecting it, which is how the
expiry is made to land in the middle of a run.
