# VCF Operations 9.1 symptom-definition retrieval

A dependency-free Go client for the VMware Cloud Foundation Operations 9.1 API.
It authenticates against the appliance and retrieves the symptom-definition
collection, which the API serves one page at a time.

## Layout

| Path                        | Role                                                              |
| --------------------------- | ----------------------------------------------------------------- |
| `opsclient/client.go`       | The client. This is the implementation file.                       |
| `docs/contract.json`        | The REST contract the client must satisfy.                         |
| `docs/official_sources.json`| Provenance of the contract: spec path, revision, and operation ids. |
| `internal/opsmock/`         | Loopback appliance mock and its fixture data, with a request log.   |
| `verifier/wire_test.go`     | Checks the client against the contract.                            |

`docs/`, `internal/opsmock/`, `verifier/` and `go.mod` are fixtures and must not
be changed.

## Contract

`docs/contract.json` is derived from `vcf-operations-openapi.json` in the
`vmware/vcf-api-specs` repository at the revision named in
`docs/official_sources.json`. Two operations are used:

- `acquireToken` — `POST /api/auth/token/acquire`
- `getSymptomDefinitions` — `GET /api/symptomdefinitions`

The `clientRules` section of the contract states how this client must use them:
the transport headers, the rule for omitting unset optional fields, how
pagination terminates, and the order entries must be emitted in.

## Running the checks

```sh
go test -race ./verifier/...
```

The mock listens on 127.0.0.1 and serves only the two operations above. No
VMware endpoint is contacted.
