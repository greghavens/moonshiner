"""Deterministic protected verifier for the VCF/VKS precheck gate."""

from __future__ import annotations

import ast
import json
import math
import secrets
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mock_api import ContractMockServer  # noqa: E402
from vcf_vks_guard import GuardedClusterClient  # noqa: E402


COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
VCENTER_IDS = ["Vcenter.Namespaces.Instances_getV2"]
CONTRACT_NAMES = [
    "getSupervisorNamespace",
    "patchVksClusterVersion",
]
KUBERNETES_KEY = (
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:patch"
)
KUBERNETES_PATCH_OPTIONALS = {
    "dryRun",
    "fieldManager",
    "fieldValidation",
    "force",
    "pretty",
}
CONFIG_STATUSES = {
    "CONFIGURING",
    "REMOVING",
    "RUNNING",
    "ERROR",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def expect_raises(
    error_types: tuple[type[BaseException], ...],
    call: Callable[[], object],
    label: str,
) -> None:
    try:
        call()
    except error_types:
        return
    except Exception as error:
        fail(f"{label}: raised unexpected {type(error).__name__}")
    fail(f"{label}: did not raise")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_source_contract() -> None:
    contract = load_json(ROOT / "docs" / "contract.json")
    sources = load_json(ROOT / "docs" / "official_sources.json")
    source = contract.get("source", {})

    require(
        source.get("repositoryCommitSha") == COMMIT,
        "contract repository commit changed",
    )
    require(
        source.get("specBlobSha") == SPEC_BLOB,
        "contract specification blob changed",
    )
    require(
        source.get("specPath") == SPEC_PATH,
        "contract specification path changed",
    )
    require(source.get("license") == "Apache-2.0", "license changed")
    require(source.get("openapi") == "3.0.3", "OpenAPI version changed")
    require(
        source.get("apiVersion") == "9.1.0.0",
        "vSphere API version changed",
    )
    require(
        source.get("serverTemplate") == "https://{host}/api"
        and source.get("basePath") == "/api",
        "vCenter server projection changed",
    )
    schemes = contract.get("securitySchemes", {})
    require(
        schemes.get("api_key_auth")
        == {
            "type": "apiKey",
            "in": "header",
            "name": "vmware-api-session-id",
        },
        "vCenter security projection changed",
    )
    require(
        schemes.get("supervisor_bearer")
        == {"type": "http", "scheme": "bearer"},
        "Kubernetes security boundary changed",
    )

    operations = contract.get("operations", [])
    require(
        [item.get("contractName") for item in operations]
        == CONTRACT_NAMES,
        "focused operation order or allow-list changed",
    )
    vcenter = operations[0]
    require(
        vcenter.get("sourceKind") == "vcenter-openapi-operation"
        and vcenter.get("operationId") == VCENTER_IDS[0]
        and vcenter.get("method") == "GET",
        "vCenter operation identity changed",
    )
    require(
        vcenter.get("specPathItem")
        == "/vcenter/namespaces/instances/v2/{namespace}"
        and vcenter.get("pathTemplate")
        == "/api/vcenter/namespaces/instances/v2/{namespace}",
        "vCenter path projection changed",
    )
    require(
        vcenter.get("requestBody") is False
        and vcenter.get("queryParameters") == [],
        "vCenter GET wire contract changed",
    )
    kubernetes = operations[1]
    require(
        kubernetes.get("sourceKind")
        == "supervisor-kubernetes-resource"
        and kubernetes.get("operationKey") == KUBERNETES_KEY
        and kubernetes.get("method") == "PATCH",
        "Kubernetes operation identity changed",
    )
    require(
        "operationId" not in kubernetes,
        "Kubernetes route claims a fictional VMware operationId",
    )
    require(
        kubernetes.get("pathTemplate")
        == (
            "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
            "{namespace}/clusters/{cluster_name}"
        ),
        "Kubernetes path changed",
    )
    require(
        kubernetes.get("requestBody", {}).get("contentType")
        == "application/merge-patch+json",
        "Kubernetes merge patch media type changed",
    )
    require(
        set(kubernetes.get("optionalQueryFields", []))
        == KUBERNETES_PATCH_OPTIONALS,
        "Kubernetes optional query projection changed",
    )

    info = contract.get("schemas", {}).get(
        "Vcenter.Namespaces.Instances.InfoV2",
        {},
    )
    require(
        info.get("required")
        == [
            "access_list",
            "config_status",
            "description",
            "messages",
            "stats",
            "storage_specs",
            "supervisor",
        ],
        "InfoV2 required properties changed",
    )
    focused = info.get("focusedProperties", {})
    require(
        set(focused) == {"supervisor", "config_status"},
        "focused InfoV2 projection changed",
    )
    require(
        set(focused.get("config_status", {}).get("enum", []))
        == CONFIG_STATUSES,
        "ConfigStatus values changed",
    )
    gate = contract.get("gate", {})
    require(
        gate.get("precheckContractName") == CONTRACT_NAMES[0]
        and gate.get("mutationContractName") == CONTRACT_NAMES[1]
        and gate.get("failedPrecheckMutationAttempts") == 0,
        "precheck gate changed",
    )
    require(
        "not represented as a VMware operationId"
        in contract.get("kubernetesProvenanceNote", ""),
        "Kubernetes provenance note changed",
    )

    require(
        sources.get("repositoryCommitSha") == COMMIT,
        "official source commit changed",
    )
    require(
        sources.get("specBlobSha") == SPEC_BLOB,
        "official source blob changed",
    )
    require(
        sources.get("specPath") == SPEC_PATH,
        "official source path changed",
    )
    require(
        sources.get("operationIds") == VCENTER_IDS,
        "official source operationId list changed",
    )
    require(sources.get("license") == "Apache-2.0", "source license changed")
    require(
        COMMIT in sources.get("specUrl", "")
        and sources["specUrl"].endswith(SPEC_PATH),
        "specification URL is not immutable",
    )
    source_operations = sources.get("operations", [])
    require(
        [item.get("operationId") for item in source_operations]
        == VCENTER_IDS,
        "official source operations changed",
    )
    for item in source_operations:
        require(
            item.get("repositoryCommitSha") == COMMIT
            and item.get("specPath") == SPEC_PATH,
            "each operation must repeat its commit and spec path",
        )


def validate_python_shape() -> None:
    require(
        sys.version_info >= (3, 11),
        "verification requires Python 3.11 or later",
    )
    path = ROOT / "vcf_vks_guard" / "client.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            root = name.split(".", 1)[0]
            require(
                root in sys.stdlib_module_names,
                f"third-party import is not allowed: {name}",
            )
    folded = source.casefold()
    require(
        "notimplementederror" not in folded,
        "client implementation is still incomplete",
    )
    for forbidden in (
        "subprocess",
        "socket.socket",
        "socket.create_connection",
        "os.system",
        "popen(",
        "curl",
    ):
        require(
            forbidden not in folded,
            f"forbidden implementation mechanism found: {forbidden}",
        )


def read_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def header_values(record: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for name, value in record["headers"]:
        result.setdefault(name, []).append(value)
    for name, values in result.items():
        require(
            len(values) == 1,
            f"request {record['sequence']} repeats header {name}",
        )
    return result


def validate_request_headers(
    records: list[dict[str, Any]],
    *,
    session_id: str,
    token: str,
    host_header: str,
) -> None:
    for record in records:
        headers = header_values(record)
        sequence = record["sequence"]
        is_vcenter = record["path"].startswith("/api/vcenter/")
        require(
            headers.get("host") == [host_header],
            f"request {sequence} has the wrong Host header",
        )
        require(
            headers.get("accept-encoding") == ["identity"],
            f"request {sequence} has the wrong transport Accept-Encoding",
        )
        require(
            headers.get("accept") == ["application/json"],
            f"request {sequence} has the wrong Accept header",
        )
        if is_vcenter:
            require(
                set(headers)
                == {
                    "host",
                    "accept-encoding",
                    "accept",
                    "vmware-api-session-id",
                },
                f"vCenter request {sequence} has extra or missing headers",
            )
            require(
                headers.get("vmware-api-session-id") == [session_id],
                f"request {sequence} has the wrong vCenter session",
            )
            require(
                "authorization" not in headers,
                f"request {sequence} leaked the Kubernetes token",
            )
            require(
                "content-type" not in headers
                and "content-length" not in headers,
                f"GET request {sequence} sent entity headers",
            )
        else:
            require(
                set(headers)
                == {
                    "host",
                    "accept-encoding",
                    "accept",
                    "authorization",
                    "content-type",
                    "content-length",
                },
                f"Kubernetes request {sequence} has extra or missing headers",
            )
            require(
                headers.get("authorization") == [f"Bearer {token}"],
                f"request {sequence} has the wrong bearer token",
            )
            require(
                "vmware-api-session-id" not in headers,
                f"request {sequence} leaked the vCenter session",
            )
            require(
                headers.get("content-type")
                == ["application/merge-patch+json"],
                f"PATCH request {sequence} has the wrong Content-Type",
            )
            require(
                headers.get("content-length")
                == [str(record["body_length"])],
                f"PATCH request {sequence} has the wrong Content-Length",
            )


def report(
    *,
    status: str,
    changed: bool,
    passed: bool,
    config_status: str,
    attempted: bool,
) -> dict[str, object]:
    return {
        "status": status,
        "changed": changed,
        "precheck": {
            "operationId": VCENTER_IDS[0],
            "passed": passed,
            "configStatus": config_status,
        },
        "mutation": {
            "operationKey": KUBERNETES_KEY,
            "attempted": attempted,
        },
    }


def main() -> None:
    validate_source_contract()
    validate_python_shape()

    suffix = secrets.token_hex(7)
    namespace = f"team blue/ñ-{suffix}"
    cluster_name = f"payments #1/{suffix}"
    supervisor = f"supervisor-{secrets.token_hex(9)}"
    old_version = f"v1.32.{secrets.randbelow(8) + 1}+vmware.1"
    target_version = (
        f"v1.33.{secrets.randbelow(8) + 1}+vmware.2-π"
    )
    session_id = secrets.token_urlsafe(27)
    token = secrets.token_urlsafe(35)
    statuses = ["ERROR", "RUNNING"]

    with tempfile.TemporaryDirectory(
        prefix="vcf-vks-guard-"
    ) as temporary:
        request_log = Path(temporary) / "requests.jsonl"
        server = ContractMockServer(
            ("127.0.0.1", 0),
            contract_path=ROOT / "docs" / "contract.json",
            request_log=request_log,
            namespace=namespace,
            supervisor=supervisor,
            cluster_name=cluster_name,
            old_version=old_version,
            namespace_statuses=statuses,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = GuardedClusterClient(
                server.root_url + "/",
                server.root_url,
                session_id,
                token,
                timeout=5.0,
            )
            require(
                read_log(request_log) == [],
                "client construction performed network traffic",
            )
            valid_arguments = {
                "supervisor": supervisor,
                "namespace": namespace,
                "cluster_name": cluster_name,
                "target_version": target_version,
            }
            for argument_name in valid_arguments:
                invalid_arguments = dict(valid_arguments)
                invalid_arguments[argument_name] = " "
                expect_raises(
                    (TypeError, ValueError),
                    lambda invalid_arguments=invalid_arguments: (
                        client.reconcile_version(**invalid_arguments)
                    ),
                    f"blank {argument_name}",
                )
            require(
                read_log(request_log) == [],
                "argument validation was not completed before traffic",
            )

            blocked = client.reconcile_version(
                supervisor=supervisor,
                namespace=namespace,
                cluster_name=cluster_name,
                target_version=target_version,
            )
            after_blocked_records = read_log(request_log)
            after_blocked_state = server.snapshot()

            succeeded = client.reconcile_version(
                supervisor=supervisor,
                namespace=namespace,
                cluster_name=cluster_name,
                target_version=target_version,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        records = read_log(request_log)
        final_state = server.snapshot()

    require(
        blocked
        == report(
            status="Blocked",
            changed=False,
            passed=False,
            config_status="ERROR",
            attempted=False,
        ),
        f"blocked precheck report differs: {blocked!r}",
    )
    require(
        len(after_blocked_records) == 1
        and after_blocked_records[0]["operation"]
        == "getSupervisorNamespace",
        "failed precheck did not stop before the mutation",
    )
    require(
        after_blocked_state
        == {
            "cluster_version": old_version,
            "namespace_get_count": 1,
            "cluster_patch_attempts": 0,
        },
        f"failed precheck changed state: {after_blocked_state!r}",
    )
    require(
        succeeded
        == report(
            status="Succeeded",
            changed=True,
            passed=True,
            config_status="RUNNING",
            attempted=True,
        ),
        f"successful report differs: {succeeded!r}",
    )
    require(
        final_state
        == {
            "cluster_version": target_version,
            "namespace_get_count": 2,
            "cluster_patch_attempts": 1,
        },
        f"successful gated state differs: {final_state!r}",
    )

    encoded_namespace = quote(namespace, safe="")
    namespace_target = (
        "/api/vcenter/namespaces/instances/v2/"
        f"{encoded_namespace}"
    )
    cluster_target = (
        "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
        f"{encoded_namespace}/clusters/{quote(cluster_name, safe='')}"
    )
    expected_transcript = [
        ("GET", namespace_target, "getSupervisorNamespace"),
        ("GET", namespace_target, "getSupervisorNamespace"),
        ("PATCH", cluster_target, "patchVksClusterVersion"),
    ]
    actual_transcript = [
        (
            record["method"],
            record["raw_target"],
            record["operation"],
        )
        for record in records
    ]
    require(
        actual_transcript == expected_transcript,
        "ordered operation transcript differs:\n"
        f"expected {expected_transcript!r}\n"
        f"actual   {actual_transcript!r}",
    )
    require(
        all(record["query"] == "" for record in records),
        "an unset optional query field or bare '?' was sent",
    )
    require(
        all("?" not in record["raw_target"] for record in records),
        "raw target contains a query delimiter",
    )

    host = urlsplit(server.root_url).netloc
    validate_request_headers(
        records,
        session_id=session_id,
        token=token,
        host_header=host,
    )
    for record in records[:2]:
        require(
            record["body_length"] == 0 and record["body_utf8"] == "",
            f"GET request {record['sequence']} was not bodyless",
        )

    patch_body = json.dumps(
        {"spec": {"topology": {"version": target_version}}},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    require(
        records[2]["body_utf8"] == patch_body
        and records[2]["body_length"]
        == len(patch_body.encode("utf-8")),
        "Kubernetes merge patch bytes differ",
    )
    patch_value = json.loads(records[2]["body_utf8"])
    require(
        set(patch_value) == {"spec"}
        and set(patch_value["spec"]) == {"topology"}
        and set(patch_value["spec"]["topology"]) == {"version"},
        "unset Kubernetes patch fields were serialized",
    )
    require(
        not any(
            option in records[2]["raw_target"]
            for option in KUBERNETES_PATCH_OPTIONALS
        ),
        "an unset Kubernetes optional field was sent",
    )

    origin = server.root_url
    constructor_cases = [
        (
            "origin path",
            (origin + "/api", origin, session_id, token, 1.0),
        ),
        (
            "credential newline",
            (origin, origin, "bad\nsession", token, 1.0),
        ),
        (
            "boolean timeout",
            (origin, origin, session_id, token, True),
        ),
        (
            "infinite timeout",
            (origin, origin, session_id, token, math.inf),
        ),
    ]
    for label, arguments in constructor_cases:
        expect_raises(
            (TypeError, ValueError),
            lambda arguments=arguments: GuardedClusterClient(
                arguments[0],
                arguments[1],
                arguments[2],
                arguments[3],
                timeout=arguments[4],
            ),
            label,
        )

    require(host.startswith("127.0.0.1:"), "verifier did not use loopback")
    print("verification passed")


if __name__ == "__main__":
    main()
