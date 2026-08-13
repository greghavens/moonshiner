# vcf-sddc-lcm-rollout

A stdlib-only Python tool that drives a staged component upgrade through the
**VCF 9.1 SDDC LCM** service and reports, per component, what actually happened.

## What is here

    fixtures/rollout-plan.json     the rollout plan the tool consumes
    tools/mock_sddc_lcm.py         loopback mock of the SDDC LCM service
    tests/test_rollout_contract.py contract test (wire shape + report)
    verify.sh                      runs the contract test

## What you add

    docs/contract.json             the REST contract, derived from the OpenAPI spec
    docs/official_sources.json     provenance for that derivation
    vcf_lcm/                       the package implementing the rollout

## Running the mock by hand

The mock builds its route table from `docs/contract.json`, so it cannot start
before that file exists. It binds loopback only and writes a JSON Lines request
log:

    python3 tools/mock_sddc_lcm.py --contract docs/contract.json \
        --log /tmp/requests.jsonl --port 8642 &
    python3 -m vcf_lcm.rollout --plan fixtures/rollout-plan.json \
        --base-url http://127.0.0.1:8642 --token dummy-token \
        --report /tmp/report.json --poll-interval 0

Each line of the log records the operation the contract matched, the method,
path, query string, request headers and the parsed request body — which is what
the contract test inspects.

## Ground rules

* Python 3.10+ standard library only. No third-party packages, no vendored code.
* `tools/`, `tests/` and `verify.sh` are fixed; the graded run restores them.
