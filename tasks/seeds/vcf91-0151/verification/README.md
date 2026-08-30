# VKS namespace change client

This repository contains a small Go client for a coordinated VCF 9.1 change:

1. read a vSphere Supervisor namespace from vCenter;
2. partially update that namespace; and
3. patch the VKS `Cluster` resource through its Kubernetes API.

The authoritative, pinned wire contract is in `docs/contract.json`. Its vCenter
operations were extracted from the Apache-2.0 `vmware/vcf-api-specs` repository;
the exact repository revision and operation IDs are recorded in
`docs/official_sources.json`.

## Task

Implement `Client.Apply` in `client.go`.

The public types and operation names in `types.go` are part of the task
contract. `Apply` must:

- validate all required client and change fields before making a request;
- perform the three steps in the order represented by `newReport`;
- use the exact paths, methods, authentication headers, content types, and JSON
  field names in `docs/contract.json`;
- treat every 2xx response as success and every other response or transport
  error as failure;
- stop after the first failure, leaving later steps `skipped`;
- preserve the successful status and HTTP status of every earlier step when a
  later step fails;
- decode and return the namespace's previous description from the GET response;
- omit unset namespace-update fields from JSON, while preserving an explicitly
  supplied empty `InfrastructurePolicies` slice as `[]`;
- include a bounded response-body summary in errors without leaking either
  credential; and
- be safe when the same `Client` is used concurrently.

Do not add third-party dependencies. Do not contact a live VMware or Kubernetes
endpoint in tests.

Run the acceptance verifier with:

```sh
bash verifier/verify.sh
```
