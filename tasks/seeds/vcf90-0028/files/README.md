# VCF 9.0 network pool reconciler

A single-file Java client that reconciles one VMware Cloud Foundation 9.0 network pool through the
SDDC Manager REST API, plus the harness that exercises it offline.

## Layout

| Path | Role |
| --- | --- |
| `src/VcfNetworkPoolClient.java` | the client — **the only file you edit** |
| `docs/contract.json` | the wire contract, derived from the pinned OpenAPI specification |
| `docs/official_sources.json` | provenance of the contract: repository, tag, commit, spec path, operation pointers |
| `harness/MockSddcManager.java` | loopback stand-in for the appliance, routed and validated from `docs/contract.json` |
| `harness/Fixture.java` | the credentials, the desired pool, and the exact bodies a conforming client sends |
| `harness/TestMain.java` | runs the scenario and writes `out/result.json` |
| `harness/MiniJson.java` | JSON reader/writer used by the harness and the verifier |
| `verify/VerifyWireShape.java` | the verifier |
| `run_verification.sh` | compile, run, verify |

`docs/`, `harness/`, `verify/` and `run_verification.sh` are protected: their SHA-256 digests are
listed in `harness/protected.sha256` and the verifier fails if any of them changed. Grading restores
them from the seed regardless.

## Running

```sh
sh run_verification.sh
```

Requires a JDK (17 or newer) on `PATH`. Nothing is downloaded and no live VMware endpoint is
contacted — the mock listens on `127.0.0.1` on an ephemeral port.

## Artifacts left behind by a run

* `out/requests.json` — every request the appliance saw: method, path, headers, raw body, status
* `out/state.json` — the appliance state at shutdown, including the network pools that exist
* `out/result.json` — what the client returned, or the exception it threw

Those three files are the committed-then-502 scenario. Matching artifacts under
`out/retryable/` cover an uncommitted 503 followed by success, and artifacts under
`out/exhausted/` cover a persistent 503. Artifacts under `out/permanent/` cover a permanent 400
rejection.

`out/requests.json` is the fastest way to see what your client actually put on the wire.

## The mock appliance

It serves only the three operations named in the contract; anything else answers 404. It requires
`Authorization: Bearer <accessToken>` on the network pool operations and validates request bodies
against the contract schemas, answering 400 with an `Error` body that names each violation.

The primary mock answers the **first** `createNetworkPool` request with `502` *after* the pool has
been committed — the appliance did the work and the response was lost on the way back. Separate
mock instances also exercise an uncommitted retryable `503` and a permanent `400`. This checks both
safe create recovery and failure classification without contacting a live endpoint. A persistent
`503` instance also verifies that the retry loop remains small and bounded.
