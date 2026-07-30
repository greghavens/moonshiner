#!/usr/bin/env python3
"""Protected verifier for vcf91-0138."""

from __future__ import annotations

import ast
import inspect
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = Path(__file__).with_name("mock_vcf_vks.py")


def fail(message: str) -> None:
    raise AssertionError(message)


def assert_equal(actual: object, expected: object, message: str) -> None:
    if actual != expected:
        fail(f"{message}: expected {expected!r}, got {actual!r}")


def assert_true(condition: object, message: str) -> None:
    if not condition:
        fail(message)


def load_object(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        fail(f"{path} must contain a JSON object")
    return value


def compact(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def validate_provenance() -> tuple[dict, dict]:
    contract = load_object(CONTRACT_PATH)
    sources = load_object(SOURCES_PATH)
    commit = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
    spec_path = "specifications/vsphere/openapi/automation/vcenter.yaml"
    blob = "8028b0824c4ff3503d05f44814f967938a795c40"
    operation_ids = [
        "Vcenter.Namespaces.Instances_createV2",
        "Vcenter.Namespaces.Instances_getV2",
    ]

    assert_equal(sources.get("repository"), "vmware/vcf-api-specs", "repository")
    assert_equal(
        sources.get("repositoryCommitSha"), commit, "pinned repository commit"
    )
    assert_equal(sources.get("specPath"), spec_path, "specification path")
    assert_equal(sources.get("specBlobSha"), blob, "specification blob")
    assert_equal(sources.get("operationIds"), operation_ids, "operationIds")
    source_operations = sources.get("operations")
    assert_true(
        isinstance(source_operations, list) and len(source_operations) == 2,
        "official_sources must record both vCenter operations",
    )
    for index, operation in enumerate(source_operations):
        assert_equal(
            operation.get("operationId"),
            operation_ids[index],
            f"official operationId {index}",
        )
        assert_equal(
            operation.get("repositoryCommitSha"),
            commit,
            f"operation commit {index}",
        )
        assert_equal(
            operation.get("specPath"),
            spec_path,
            f"operation spec path {index}",
        )

    source = contract.get("source")
    assert_true(isinstance(source, dict), "contract source object")
    assert_equal(source.get("commitSha"), commit, "contract commit")
    assert_equal(source.get("specPath"), spec_path, "contract spec path")
    assert_equal(source.get("specBlobSha"), blob, "contract spec blob")
    assert_equal(source.get("apiVersion"), "9.1.0.0", "contract API version")

    vcenter_operations = contract.get("operations")
    assert_true(
        isinstance(vcenter_operations, list) and len(vcenter_operations) == 2,
        "focused contract must contain two spec-derived vCenter operations",
    )
    kubernetes_api = contract.get("supervisorKubernetesApi")
    assert_true(
        isinstance(kubernetes_api, dict),
        "focused contract must name the Supervisor Kubernetes API separately",
    )
    assert_equal(
        kubernetes_api.get("apiVersion"),
        "cluster.x-k8s.io/v1beta2",
        "Supervisor Cluster resource API version",
    )
    kubernetes_operations = kubernetes_api.get("operations")
    assert_true(
        isinstance(kubernetes_operations, list)
        and len(kubernetes_operations) == 2,
        "focused contract must contain two Kubernetes resource operations",
    )
    operations = vcenter_operations + kubernetes_operations
    expected_operations = [
        (
            "createSupervisorNamespace",
            operation_ids[0],
            "POST",
            "/api/vcenter/namespaces/instances/v2",
        ),
        (
            "getSupervisorNamespace",
            operation_ids[1],
            "GET",
            "/api/vcenter/namespaces/instances/v2/{namespace}",
        ),
        (
            "createVksCluster",
            None,
            "POST",
            "/apis/cluster.x-k8s.io/v1beta2/namespaces/{namespace}/clusters",
        ),
        (
            "getVksCluster",
            None,
            "GET",
            (
                "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
                "{namespace}/clusters/{cluster_name}"
            ),
        ),
    ]
    for operation, expected in zip(operations, expected_operations):
        name, operation_id, method, path = expected
        assert_equal(operation.get("contractName"), name, f"{name} name")
        assert_equal(operation.get("operationId"), operation_id, f"{name} id")
        assert_equal(operation.get("method"), method, f"{name} method")
        assert_equal(operation.get("pathTemplate"), path, f"{name} path")

    schemas = contract.get("schemas")
    assert_true(isinstance(schemas, dict), "contract schemas object")
    create_spec = schemas.get("Vcenter.Namespaces.Instances.CreateSpecV2")
    assert_true(isinstance(create_spec, dict), "CreateSpecV2 projection")
    assert_equal(
        create_spec.get("required"),
        ["namespace", "supervisor"],
        "CreateSpecV2 required fields",
    )
    expected_create_properties = [
        "supervisor",
        "zones",
        "network_spec",
        "namespace",
        "description",
        "resource_spec",
        "access_list",
        "storage_specs",
        "networks",
        "vm_service_spec",
        "content_libraries",
        "creator",
        "namespace_network",
        "edges",
        "infrastructure_policies",
    ]
    assert_equal(
        list(create_spec.get("properties", {})),
        expected_create_properties,
        "CreateSpecV2 property projection",
    )
    info = schemas.get("Vcenter.Namespaces.Instances.InfoV2")
    assert_true(isinstance(info, dict), "InfoV2 projection")
    assert_equal(
        info.get("required"),
        [
            "access_list",
            "config_status",
            "description",
            "messages",
            "stats",
            "storage_specs",
            "supervisor",
        ],
        "InfoV2 required fields",
    )
    status = schemas.get("Vcenter.Namespaces.Instances.ConfigStatus")
    assert_true(isinstance(status, dict), "ConfigStatus projection")
    assert_equal(
        status.get("enum"),
        ["CONFIGURING", "REMOVING", "RUNNING", "ERROR"],
        "ConfigStatus enum",
    )
    return contract, sources


def validate_stdlib_only() -> None:
    package = ROOT / "vcf_vks"
    files = sorted(package.rglob("*.py"))
    assert_true(files, "vcf_vks Python package exists")
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as error:
            fail(f"{path.relative_to(ROOT)} does not parse: {error}")
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for name in names:
                top_level = name.split(".", 1)[0]
                if top_level and top_level not in stdlib:
                    fail(
                        f"{path.relative_to(ROOT)} imports non-stdlib module "
                        f"{top_level!r}"
                    )
    forbidden_suffixes = {".whl", ".zip", ".tar", ".gz", ".yaml", ".yml"}
    vendored = [
        path
        for path in package.rglob("*")
        if path.is_file() and path.suffix.lower() in forbidden_suffixes
    ]
    assert_equal(vendored, [], "package must not vendor dependencies or specs")


def validate_api(module: object) -> None:
    expected_exports = {
        "VksProvisioner",
        "ApiError",
        "NamespaceFailedError",
        "ClusterFailedError",
        "ProvisionTimeoutError",
    }
    assert_equal(set(module.__all__), expected_exports, "package exports")
    signature = inspect.signature(module.VksProvisioner.provision_cluster)
    expected_names = [
        "self",
        "namespace",
        "supervisor",
        "cluster_name",
        "kubernetes_version",
        "vm_class",
        "storage_class",
        "cluster_class",
        "control_plane_replicas",
        "worker_replicas",
        "description",
        "namespace_storage_policy",
        "labels",
        "poll_interval",
        "timeout",
    ]
    assert_equal(list(signature.parameters), expected_names, "workflow signature")
    for name in expected_names[1:]:
        assert_equal(
            signature.parameters[name].kind,
            inspect.Parameter.KEYWORD_ONLY,
            f"{name} is keyword-only",
        )
    expected_defaults = {
        "cluster_class": "builtin-generic-v3.6.0",
        "control_plane_replicas": 1,
        "worker_replicas": 3,
        "description": None,
        "namespace_storage_policy": None,
        "labels": None,
        "poll_interval": 1.0,
        "timeout": 900.0,
    }
    for name, expected in expected_defaults.items():
        assert_equal(
            signature.parameters[name].default,
            expected,
            f"{name} default",
        )


def wait_for_port(process: subprocess.Popen[bytes], port_path: Path) -> int:
    deadline = time.monotonic() + 10.0
    while not port_path.exists():
        if process.poll() is not None:
            stderr = process.stderr.read().decode("utf-8", errors="replace")
            fail(f"loopback mock exited before startup: {stderr}")
        if time.monotonic() >= deadline:
            fail("timed out waiting for loopback mock startup")
        time.sleep(0.02)
    return int(port_path.read_text(encoding="ascii"))


def validate_log(
    log_path: Path,
    *,
    namespace: str,
    cluster_name: str,
    session_id: str,
    bearer_token: str,
    namespace_body: dict,
    cluster_body: dict,
) -> None:
    lines = log_path.read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines if line.strip()]
    assert_equal(len(entries), 6, "exact request count")
    names = [entry["contractName"] for entry in entries]
    assert_equal(
        names,
        [
            "createSupervisorNamespace",
            "getSupervisorNamespace",
            "getSupervisorNamespace",
            "createVksCluster",
            "getVksCluster",
            "getVksCluster",
        ],
        "operation order and polling",
    )
    assert_equal(
        [entry["method"] for entry in entries],
        ["POST", "GET", "GET", "POST", "GET", "GET"],
        "HTTP method order",
    )
    assert_equal(
        [entry["status"] for entry in entries],
        [204, 200, 200, 201, 200, 200],
        "fixture response sequence",
    )
    assert_true(
        all(entry["requestValid"] is True for entry in entries),
        "every request matches the focused contract",
    )
    escaped_namespace = quote(namespace, safe="")
    escaped_cluster = quote(cluster_name, safe="")
    expected_targets = [
        "/api/vcenter/namespaces/instances/v2",
        f"/api/vcenter/namespaces/instances/v2/{escaped_namespace}",
        f"/api/vcenter/namespaces/instances/v2/{escaped_namespace}",
        (
            "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
            f"{escaped_namespace}/clusters"
        ),
        (
            "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
            f"{escaped_namespace}/clusters/{escaped_cluster}"
        ),
        (
            "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
            f"{escaped_namespace}/clusters/{escaped_cluster}"
        ),
    ]
    assert_equal(
        [entry["rawTarget"] for entry in entries],
        expected_targets,
        "exact request targets",
    )
    assert_true(
        all(entry["rawQuery"] == "" for entry in entries),
        "no operation sends a query string",
    )
    assert_true(
        all(entry["accept"] == "application/json" for entry in entries),
        "every request sends JSON Accept",
    )

    for entry in entries[:3]:
        assert_equal(
            entry["vmwareApiSessionId"],
            session_id,
            "vCenter session header",
        )
        assert_equal(entry["authorization"], None, "no bearer on vCenter")
    for entry in entries[3:]:
        assert_equal(
            entry["authorization"],
            f"Bearer {bearer_token}",
            "Supervisor bearer header",
        )
        assert_equal(
            entry["vmwareApiSessionId"],
            None,
            "no vCenter session on Supervisor API",
        )

    expected_namespace_bytes = compact(namespace_body)
    expected_cluster_bytes = compact(cluster_body)
    assert_equal(
        entries[0]["bodyHex"],
        expected_namespace_bytes.hex(),
        "byte-exact namespace body",
    )
    assert_equal(
        entries[3]["bodyHex"],
        expected_cluster_bytes.hex(),
        "byte-exact Cluster body",
    )
    assert_equal(
        entries[0]["contentType"],
        "application/json",
        "namespace content type",
    )
    assert_equal(
        entries[3]["contentType"],
        "application/json",
        "Cluster content type",
    )
    for index in (1, 2, 4, 5):
        assert_equal(entries[index]["contentLength"], 0, f"GET {index} body")
        assert_equal(entries[index]["bodyHex"], "", f"GET {index} body bytes")
        assert_equal(
            entries[index]["contentType"],
            None,
            f"GET {index} has no content type",
        )

    decoded_namespace = json.loads(bytes.fromhex(entries[0]["bodyHex"]))
    assert_equal(
        list(decoded_namespace),
        ["supervisor", "namespace"],
        "namespace body keys and order",
    )
    optional_namespace_fields = {
        "zones",
        "network_spec",
        "description",
        "resource_spec",
        "access_list",
        "storage_specs",
        "networks",
        "vm_service_spec",
        "content_libraries",
        "creator",
        "namespace_network",
        "edges",
        "infrastructure_policies",
    }
    assert_true(
        optional_namespace_fields.isdisjoint(decoded_namespace),
        "unset CreateSpecV2 fields are omitted",
    )
    decoded_cluster = json.loads(bytes.fromhex(entries[3]["bodyHex"]))
    assert_equal(
        list(decoded_cluster),
        ["apiVersion", "kind", "metadata", "spec"],
        "Cluster top-level member order",
    )
    assert_equal(
        list(decoded_cluster["metadata"]),
        ["name", "namespace"],
        "Cluster metadata member order and omitted labels",
    )
    topology = decoded_cluster["spec"]["topology"]
    assert_equal(
        list(topology),
        ["class", "version", "controlPlane", "workers", "variables"],
        "Cluster topology member order",
    )
    assert_equal(
        [variable["name"] for variable in topology["variables"]],
        ["vmClass", "storageClass"],
        "Cluster variable order",
    )


def main() -> int:
    validate_provenance()
    validate_stdlib_only()
    sys.path.insert(0, str(ROOT))
    import vcf_vks

    validate_api(vcf_vks)

    run_id = uuid.uuid4().hex
    namespace = f"team-{run_id[:8]}"
    supervisor = f"supervisor-{run_id[8:16]}"
    cluster_name = f"orders-{run_id[16:24]}"
    session_id = f"session-{run_id}"
    bearer_token = f"bearer-{run_id[::-1]}"
    kubernetes_version = f"v1.35.{int(run_id[0], 16)}---vmware.2-vkr.4"
    vm_class = f"best-effort-{run_id[1:7]}"
    storage_class = f"gold-{run_id[7:13]}"
    namespace_body = {
        "supervisor": supervisor,
        "namespace": namespace,
    }
    cluster_body = {
        "apiVersion": "cluster.x-k8s.io/v1beta2",
        "kind": "Cluster",
        "metadata": {
            "name": cluster_name,
            "namespace": namespace,
        },
        "spec": {
            "topology": {
                "class": "builtin-generic-v3.6.0",
                "version": kubernetes_version,
                "controlPlane": {"replicas": 1},
                "workers": {
                    "machineDeployments": [
                        {
                            "class": "node-pool",
                            "name": "workers",
                            "replicas": 3,
                        }
                    ]
                },
                "variables": [
                    {"name": "vmClass", "value": vm_class},
                    {"name": "storageClass", "value": storage_class},
                ],
            }
        },
    }
    scenario = {
        "namespace": namespace,
        "supervisor": supervisor,
        "cluster_name": cluster_name,
        "session_id": session_id,
        "bearer_token": bearer_token,
        "expected_namespace_body": namespace_body,
        "expected_cluster_body": cluster_body,
    }

    with tempfile.TemporaryDirectory(prefix="vcf91-0138-") as temporary:
        temp_root = Path(temporary)
        port_path = temp_root / "port.txt"
        log_path = temp_root / "requests.jsonl"
        scenario_path = temp_root / "scenario.json"
        scenario_path.write_bytes(compact(scenario))
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK_PATH),
                str(port_path),
                str(log_path),
                str(CONTRACT_PATH),
                str(scenario_path),
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        try:
            port = wait_for_port(process, port_path)
            base_url = f"http://127.0.0.1:{port}/"
            client = vcf_vks.VksProvisioner(
                base_url,
                base_url,
                session_id,
                bearer_token,
                timeout=3.0,
            )
            assert_true(
                not log_path.exists(), "client construction performs no request"
            )
            final_cluster = client.provision_cluster(
                namespace=namespace,
                supervisor=supervisor,
                cluster_name=cluster_name,
                kubernetes_version=kubernetes_version,
                vm_class=vm_class,
                storage_class=storage_class,
                poll_interval=0.001,
                timeout=5.0,
            )
            assert_true(isinstance(final_cluster, dict), "final Cluster object")
            assert_equal(
                final_cluster.get("apiVersion"),
                "cluster.x-k8s.io/v1beta2",
                "final Cluster apiVersion",
            )
            assert_equal(
                final_cluster.get("kind"), "Cluster", "final resource kind"
            )
            assert_equal(
                final_cluster.get("metadata", {}).get("name"),
                cluster_name,
                "final Cluster name",
            )
            ready_conditions = [
                condition
                for condition in final_cluster.get("status", {}).get(
                    "conditions", []
                )
                if condition.get("type") == "Ready"
            ]
            assert_equal(len(ready_conditions), 1, "one final Ready condition")
            assert_equal(
                ready_conditions[0].get("status"),
                "True",
                "terminal Ready condition",
            )
            validate_log(
                log_path,
                namespace=namespace,
                cluster_name=cluster_name,
                session_id=session_id,
                bearer_token=bearer_token,
                namespace_body=namespace_body,
                cluster_body=cluster_body,
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    print("verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
