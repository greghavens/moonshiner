# VKS Supervisor deployment module

Implement `src/VksSupervisor.psm1`. The manifest already exports
`Invoke-VksClusterDeployment` and declares
`VMware.Sdk.Vcf.SddcManager` as an external VCF PowerCLI prerequisite.
Do not vendor or replace VMware's module.

The function signature is provided in the source stub. Its workflow is:

1. `POST` the vSphere Namespace `CreateSpecV2`.
2. Poll the corresponding namespace `getV2` operation. Continue while
   `config_status` is `CONFIGURING`, proceed only at `RUNNING`, and fail on
   `ERROR` or `REMOVING`.
3. Create a VKS `cluster.x-k8s.io/v1beta1` `Cluster` in that Supervisor
   namespace.
4. Poll the Cluster resource. A `Provisioned` phase alone is not completion:
   return only when the `Ready` condition has string status `True`. Treat
   `Failed` and `Deleting` phases as terminal errors.
5. Apply one timeout budget to each polling phase and honor
   `PollIntervalMilliseconds`.

Use these wire-level rules:

- `VCenterBaseUri` includes the vCenter `/api` prefix. Send the vCenter
  credential only as `vmware-api-session-id`.
- `KubernetesBaseUri` is the Kubernetes API origin. Send its credential only
  as `Authorization: Bearer <token>`.
- Send `Accept: application/json` on every request and
  `Content-Type: application/json` only when a JSON request body exists.
- URI-escape path values.
- Build the namespace body from `supervisor`, `namespace`, and one
  `storage_specs` item containing `policy`. Include `description` only when it
  was supplied. Include the storage item's `limit` only when
  `NamespaceStorageLimitMiB` was supplied.
- Build the VKS body from `apiVersion`, `kind`, `metadata`, and
  `spec.topology`. The topology contains `class`, `version`,
  `controlPlane.replicas`, one worker machine deployment, and the ordered
  variables `vmClass` and `storageClass`.
- Include `spec.clusterNetwork` only when both `ServiceCidr` and `PodCidr`
  were supplied. Supplying only one is an argument error. When present it has
  `services.cidrBlocks`, `pods.cidrBlocks`, and
  `serviceDomain: cluster.local`.
- Never serialize an unset option as `null`, an empty string, an empty array,
  or an empty object. Do not leak one control plane's credential to the other.

Return one object after readiness with at least `Namespace`,
`NamespaceStatus`, `Cluster`, `ClusterPhase`, `Ready`,
`NamespacePollCount`, and `ClusterPollCount`.

The normative vCenter subset is in `docs/contract.json`. Its two exact
operationIds, source path, and pinned repository commit are recorded in
`docs/official_sources.json`. The separately labeled Kubernetes routes reflect
the VKS Cluster API surface and are not represented as VMware operationIds.

Run:

```text
python3 grader_tests/verify.py
```

The verifier launches only the loopback mock in `tools/mock_server.py`; it
does not contact a live VMware endpoint.
