# VCF Operations for Networks 9.1 — application tier client

A small client for the application-tier slice of the VCF Operations for Networks API, the successor
to vRealize Network Insight.

## Layout

| Path | Purpose |
| --- | --- |
| `docs/contract.json` | The pinned operation subset, schemas and wire encoding rules. |
| `docs/official_sources.json` | Where the contract came from: repository, commit and per-operation line ranges. |
| `src/NiTierClient.java` | The client. This is the file to implement. |
| `src/Json.java` | Supplied JSON codec. `write` preserves `LinkedHashMap` key order and emits compact JSON. |
| `tests/MockNiServer.java` | Offline in-process HTTP contract fixture serving only the three pinned operations, with a request log. |
| `tests/TestMain.java` | Harness that drives the client against the fixture and prints the recorded requests. |
| `tests/verify.py` | Compiles everything, runs the harness and judges the requests. |

## Running

```sh
python3 tests/verify.py
```

The fixture implements a JDK `HttpClient` in process and keeps all state in memory. It receives the
real `HttpRequest` objects built by the client without opening a socket. Nothing in this project
contacts a VMware appliance.

## Building by hand

```sh
javac -d build src/*.java tests/*.java && java -cp build TestMain
```
