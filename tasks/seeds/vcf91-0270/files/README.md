# VCF Operations custom group client

A small Go module that talks to the custom group operations of the **VMware
Cloud Foundation Operations** API (the `suite-api` served by a VCF Operations
appliance, not the separate log management API).

## Layout

| Path | Purpose |
| --- | --- |
| `docs/contract.json` | The slice of the product's OpenAPI specification this module is built against. Generated, do not hand-edit. |
| `docs/official_sources.json` | Provenance for the contract: repository, spec path, commit sha, licence and the operationIds it was derived for. |
| `customgroup/` | The client package. **This is the package to implement.** |
| `internal/contract/` | Loads and interprets `docs/contract.json`. |
| `internal/mock/` | Loopback stand-in for an appliance, pinned to the contract. |
| `verify/` | Protected acceptance checks. |

`docs/`, `internal/` and `verify/` are fixed. Implement `customgroup/` and add
the package's own tests there.

## The contract

`docs/contract.json` was generated from
`specifications/vcf-operations/vcf-operations-openapi.json` in
[vmware/vcf-api-specs](https://github.com/vmware/vcf-api-specs) (Apache-2.0), at
the commit recorded in `docs/official_sources.json`. It carries the two
operationIds this module uses, their parameters and the verbatim schema closure
they reference:

- `getCustomGroups` — `GET /suite-api/api/resources/groups`
- `createCustomGroup` — `POST /suite-api/api/resources/groups`

Read the property names, the `required` lists and the parameter definitions out
of the contract rather than guessing them. Nothing outside those two operations
may be called.

Two details the contract states but that are easy to miss:

- The `custom-group` schema marks **`resourceKey` and `membershipDefinition`
  required**. Both belong in every create request, even an empty membership.
- `getCustomGroups` declares `groupId` and `includePolicy` as optional query
  parameters, and gives `includePolicy` the default `false`. Neither carries a
  `style` or `explode` annotation, so the OpenAPI 3 default applies: `form` with
  `explode: true`, meaning an array repeats the parameter
  (`?groupId=a&groupId=b`).

## The mock appliance

`internal/mock` starts an `httptest` server on `127.0.0.1` and routes only from
`docs/contract.json`. Anything else — a different path, a different verb, a
query parameter the operation does not declare, a body property outside the
schema — is refused. Every request, refused ones included, is appended to a log:

```go
srv := mock.New(t)
c, err := customgroup.NewClient(srv.URL(), srv.Authorization(), srv.Client())
// ... exercise the client ...
for _, r := range srv.RequestsFor("createCustomGroup") {
    body, _ := r.BodyMap() // the exact JSON object that was sent
}
```

Useful methods: `URL`, `Client`, `Authorization`, `Requests`, `RequestsFor`,
`CountFor`, `Groups`, `GroupNames`, `Reset`.

### Behaviour beyond the specification

The OpenAPI document describes wire shape, not the appliance's conflict
handling. The mock adds two behaviours so that retry safety is observable, and
they are documented on the package rather than in the contract:

- `createCustomGroup` answers **409 Conflict** when a group already exists with
  the same `resourceKey` triple of name, adapter kind and resource kind. A real
  appliance likewise refuses to hold two custom groups under one resource key.
- `FailNextCreateAfterStore()` stores the group and *then* fails with 503,
  modelling a response lost after the write committed.
  `InsertBeforeNextCreate(...)` slips a group in just before the next create,
  modelling another writer that won the race.

## Running the checks

```sh
go test -race ./...
```

No test reaches the network; every request terminates on loopback.
