# vcfon-datasource-onboarding

A small integration client for **VMware Cloud Foundation Operations for Networks 9.1** (the
successor to vRealize Network Insight). It onboards a vCenter Server as a data source, and it runs
the product's own precheck first so that a vCenter which cannot be validated is never half-created.

## Layout

```
docs/contract.json           the wire contract this client must speak
docs/official_sources.json   where that contract came from (OpenAPI document + revision)
src/main/java/...            the client -- one file, no dependencies
harness/src/harness/         test harness: JSON codec, loopback mock, TestMain, Verifier
tools/protected.sha256       digests of the files the harness owns
run_tests.sh                 compile, run the scenarios, verify
```

## The contract

`docs/contract.json` is derived from the OpenAPI document that ships in the
[`vmware/vcf-api-specs`](https://github.com/vmware/vcf-api-specs) repository (Apache-2.0), at
`specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml`. The exact revision, the
commit sha and every `operationId` that was lifted from it are recorded in
`docs/official_sources.json`. Four operations are in scope:

| operationId            | method | path                                 | role      |
| ---------------------- | ------ | ------------------------------------ | --------- |
| `create`               | POST   | `/auth/token`                        | auth      |
| `listExpandedNodes`    | GET    | `/infra/expanded-nodes`              | lookup    |
| `validateVCenter`      | POST   | `/data-sources/vcenters/validate`    | precheck  |
| `addVcenterDatasource` | POST   | `/data-sources/vcenters`             | mutation  |

Paths are relative to the `/api/ni` base path declared by the document's `servers` entry.

Two details of this API bite people:

* `validateVCenter` answers **HTTP 200 even when validation fails**. The verdict lives in the
  response body's `code` field.
* Optional request fields have server-side defaults. Sending one as `null`, `""`, `{}` or `[]` is
  not the same as leaving it out, and the appliance rejects it.

## Running

```sh
./run_tests.sh
```

The harness starts a fresh loopback mock per scenario on `127.0.0.1`, pinned to
`docs/contract.json`, and serves only the four operations above -- anything else answers 404. Each
mock writes a JSONL request log to `build/requests-<scenario>.jsonl`, and `TestMain` writes a
summary to `build/report.json`. `Verifier` then reads both back and checks the exact wire shape of
every request, plus the precheck gate. No live VMware endpoint is contacted.
