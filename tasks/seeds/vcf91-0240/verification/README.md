# vcf-lcm-rollout

Fleet lifecycle tooling for VMware Cloud Foundation 9.1. This corner of the repo drives an
SDDC LCM component upgrade from a change request, against the VCF 9.1 SDDC LCM service.

## Layout

```
docs/contract.json          the SDDC LCM operations this tool is allowed to call,
                            derived from the published OpenAPI specification
docs/official_sources.json  where that contract came from (repo, commit, operationIds)
docs/client_api.md          the client entry point and the rollout report format
fixtures/upgrade-request.json  the operator's change request for this rollout
harness/Json.java           minimal JSON reader/writer, on the classpath for the client
harness/TestMain.java       drives the client and writes build/report.json
mock/SddcLcmMock.java       loopback SDDC LCM fixture; serves only the contract's
                            operations and records every request it receives
src/VcfLcmClient.java       the client  <-- the only file to change
run.sh                      compile, start the fixture, run one rollout, stop the fixture
verify.py                   contract and rollout checks
```

## Running

```
./run.sh          # leaves build/requests.jsonl, build/report.json, build/mock.log
python3 verify.py # independent build + run + checks
```

Both bind the fixture to `127.0.0.1` on an ephemeral port and reach nothing else.

## Notes

* `docs/contract.json` is generated from the specification and is the authority on paths,
  methods, parameters, required properties and optional properties. The fixture validates
  incoming requests against it and answers `400` when a request strays.
* `build/requests.jsonl` holds one JSON object per request the fixture received, including
  the parsed body, so you can see exactly what went on the wire.
* The fixture's tasks advance one step per `getTask` poll, the way a real service advances
  over time.
