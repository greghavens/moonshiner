"""Deterministic protected verifier for the VCF/VKS partial-success task."""

from __future__ import annotations

import ast
import copy
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
from vcf_vks_change import (  # noqa: E402
    CoordinatedChangeClient,
)


COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
VCENTER_IDS = [
    "Vcenter.Namespaces.Instances_getV2",
    "Vcenter.Namespaces.Instances_update",
]
CONTRACT_NAMES = [
    "getSupervisorNamespace",
    "getVksCluster",
    "updateSupervisorNamespace",
    "patchVksClusterVersion",
]
KUBERNETES_KEYS = [
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:get",
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:patch",
]
UPDATE_OPTIONALS = {
    "description",
    "resource_spec",
    "access_list",
    "storage_specs",
    "vm_service_spec",
    "content_libraries",
    "network_spec",
    "zones",
    "edges",
    "infrastructure_policies",
}
KUBERNETES_PATCH_OPTIONALS = {
    "dryRun",
    "fieldManager",
    "fieldValidation",
    "force",
    "pretty",
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
) -> BaseException:
    try:
        call()
    except error_types as error:
        return error
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
    require(source.get("basePath") == "/api", "base path changed")
    require(
        contract.get("securitySchemes", {}).get("api_key_auth")
        == {
            "type": "apiKey",
            "in": "header",
            "name": "vmware-api-session-id",
        },
        "vCenter security projection changed",
    )

    operations = contract.get("operations", [])
    require(
        [item.get("contractName") for item in operations]
        == CONTRACT_NAMES,
        "focused operation order or allow-list changed",
    )
    vcenter = [
        item
        for item in operations
        if item.get("sourceKind") == "vcenter-openapi-operation"
    ]
    require(
        [item.get("operationId") for item in vcenter] == VCENTER_IDS,
        "vCenter operationIds changed",
    )
    require(
        [
            (item.get("method"), item.get("specPathItem"), item.get("pathTemplate"))
            for item in vcenter
        ]
        == [
            (
                "GET",
                "/vcenter/namespaces/instances/v2/{namespace}",
                "/api/vcenter/namespaces/instances/v2/{namespace}",
            ),
            (
                "PATCH",
                "/vcenter/namespaces/instances/{namespace}",
                "/api/vcenter/namespaces/instances/{namespace}",
            ),
        ],
        "vCenter focused paths or methods changed",
    )
    kubernetes = [
        item
        for item in operations
        if item.get("sourceKind") == "supervisor-kubernetes-resource"
    ]
    require(
        [item.get("operationKey") for item in kubernetes]
        == KUBERNETES_KEYS,
        "Kubernetes operation keys changed",
    )
    require(
        not any("operationId" in item for item in kubernetes),
        "Kubernetes routes claim fictional VMware operationIds",
    )
    require(
        "not represented as VMware operationIds"
        in contract.get("kubernetesProvenanceNote", ""),
        "Kubernetes provenance note changed",
    )
    require(
        kubernetes[1]["requestBody"]["contentType"]
        == "application/merge-patch+json",
        "Kubernetes merge patch media type changed",
    )
    require(
        set(kubernetes[1]["optionalQueryFields"])
        == KUBERNETES_PATCH_OPTIONALS,
        "Kubernetes optional query projection changed",
    )

    info = contract.get("schemas", {}).get(
        "Vcenter.Namespaces.Instances.InfoV2",
        {},
    )
    expected_required = [
        "access_list",
        "config_status",
        "description",
        "messages",
        "stats",
        "storage_specs",
        "supervisor",
    ]
    require(
        info.get("required") == expected_required,
        "InfoV2 required properties changed",
    )
    require(
        set(info.get("properties", {})) == set(expected_required),
        "focused InfoV2 property projection changed",
    )
    update = contract.get("schemas", {}).get(
        "Vcenter.Namespaces.Instances.UpdateSpec",
        {},
    )
    require(update.get("partialUpdate") is True, "UpdateSpec is not partial")
    require(
        set(update.get("properties", {})) == UPDATE_OPTIONALS,
        "UpdateSpec property projection changed",
    )
    require(
        all(
            item.get("required") is False
            and item.get("unsetBehavior") == "omit-and-leave-unchanged"
            for item in update["properties"].values()
        ),
        "UpdateSpec omission semantics changed",
    )
    require(
        update["properties"]["edges"].get("addedIn") == "9.1.0.0"
        and update["properties"]["infrastructure_policies"].get("addedIn")
        == "9.1.0.0",
        "VCF 9.1 UpdateSpec fields changed",
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
        [item.get("operationId") for item in source_operations] == VCENTER_IDS,
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
    path = ROOT / "vcf_vks_change" / "client.py"
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
        if isinstance(node, ast.Import):
            require(
                not any(
                    alias.name.split(".", 1)[0] == "subprocess"
                    for alias in node.names
                ),
                "subprocess is not allowed",
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            require(
                node.module.split(".", 1)[0] != "subprocess",
                "subprocess is not allowed",
            )
            if node.module == "socket":
                require(
                    not any(
                        alias.name in {"socket", "create_connection"}
                        for alias in node.names
                    ),
                    "raw socket calls are not allowed",
                )
            if node.module == "os":
                require(
                    not any(
                        alias.name in {"system", "popen"}
                        for alias in node.names
                    ),
                    "shell process calls are not allowed",
                )
        if isinstance(node, ast.Name):
            require(
                node.id != "NotImplementedError",
                "client implementation is still incomplete",
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if isinstance(owner, ast.Name):
                qualified = f"{owner.id}.{node.func.attr}"
                require(
                    qualified
                    not in {
                        "socket.socket",
                        "socket.create_connection",
                        "os.system",
                        "os.popen",
                    },
                    f"forbidden implementation mechanism found: {qualified}",
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
) -> None:
    for record in records:
        headers = header_values(record)
        sequence = record["sequence"]
        require(
            headers.get("accept") == ["application/json"],
            f"request {sequence} has the wrong Accept header",
        )
        is_vcenter = record["path"].startswith("/api/vcenter/")
        if is_vcenter:
            require(
                headers.get("vmware-api-session-id") == [session_id],
                f"request {sequence} has the wrong vCenter session",
            )
            require(
                "authorization" not in headers,
                f"request {sequence} leaked the Kubernetes token",
            )
        else:
            require(
                headers.get("authorization") == [f"Bearer {token}"],
                f"request {sequence} has the wrong bearer token",
            )
            require(
                "vmware-api-session-id" not in headers,
                f"request {sequence} leaked the vCenter session",
            )

        if record["method"] == "GET":
            require(
                "content-type" not in headers,
                f"GET request {sequence} sent Content-Type",
            )
            require(
                "content-length" not in headers,
                f"GET request {sequence} sent Content-Length",
            )
        else:
            expected_type = (
                "application/json"
                if is_vcenter
                else "application/merge-patch+json"
            )
            require(
                headers.get("content-type") == [expected_type],
                f"PATCH request {sequence} has the wrong Content-Type",
            )
            require(
                headers.get("content-length")
                == [str(record["body_length"])],
                f"PATCH request {sequence} has the wrong Content-Length",
            )
        require(
            "transfer-encoding" not in headers
            and "content-encoding" not in headers,
            f"request {sequence} changed the entity framing",
        )


def main() -> None:
    validate_source_contract()
    validate_python_shape()

    suffix = secrets.token_hex(7)
    namespace = f"team blue/ñ-{suffix}"
    cluster_name = f"payments #1/{suffix}"
    supervisor = f"supervisor-{secrets.token_hex(9)}"
    cluster_class = f"builtin-generic-v{secrets.randbelow(6) + 3}.0"
    old_description = f"before-{secrets.token_hex(8)}"
    namespace_description = f"new description π/{secrets.token_hex(8)}"
    old_version = f"v1.32.{secrets.randbelow(8) + 1}+vmware.1"
    target_version = f"v1.33.{secrets.randbelow(8) + 1}+vmware.2"
    session_id = secrets.token_urlsafe(27)
    token = secrets.token_urlsafe(35)
    failure_marker = f"never-expose-{secrets.token_urlsafe(24)}"

    with tempfile.TemporaryDirectory(
        prefix="vcf-vks-change-"
    ) as temporary:
        request_log = Path(temporary) / "requests.jsonl"
        server = ContractMockServer(
            ("127.0.0.1", 0),
            contract_path=ROOT / "docs" / "contract.json",
            request_log=request_log,
            namespace=namespace,
            supervisor=supervisor,
            cluster_name=cluster_name,
            cluster_class=cluster_class,
            old_description=old_description,
            old_version=old_version,
            failure_marker=failure_marker,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = CoordinatedChangeClient(
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
                "cluster_class": cluster_class,
                "namespace_description": namespace_description,
                "target_version": target_version,
            }
            for argument_name in valid_arguments:
                for invalid in (None, "", " \t"):
                    arguments = dict(valid_arguments)
                    arguments[argument_name] = invalid
                    validation_error = expect_raises(
                        (TypeError, ValueError),
                        lambda arguments=arguments: client.coordinate_change(
                            **arguments
                        ),
                        f"invalid {argument_name}",
                    )
                    require(
                        session_id not in str(validation_error)
                        and token not in str(validation_error),
                        f"invalid {argument_name}: exception leaked credentials",
                    )
            require(
                read_log(request_log) == [],
                "argument validation was not completed before traffic",
            )

            result = client.coordinate_change(
                supervisor=supervisor,
                namespace=namespace,
                cluster_name=cluster_name,
                cluster_class=cluster_class,
                namespace_description=namespace_description,
                target_version=target_version,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        records = read_log(request_log)
        state = server.snapshot()

    expected_result = {
        "overallStatus": "Failed",
        "namespaceChange": {
            "operationId": "Vcenter.Namespaces.Instances_update",
            "namespace": namespace,
            "status": "Succeeded",
            "changed": True,
        },
        "clusterChange": {
            "operationKey": (
                "cluster.x-k8s.io/v1beta2:"
                "namespaced-clusters:patch"
            ),
            "namespace": namespace,
            "name": cluster_name,
            "status": "Failed",
            "changed": False,
            "httpStatus": 422,
        },
    }
    require(
        result == expected_result,
        f"partial-success ledger differs: {result!r}",
    )
    rendered_result = json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
    )
    for secret in (session_id, token, failure_marker):
        require(secret not in rendered_result, "result leaked sensitive data")

    require(
        state
        == {
            "namespace_description": namespace_description,
            "cluster_version": old_version,
            "namespace_update_count": 1,
            "cluster_patch_attempts": 1,
        },
        f"committed/rejected state boundary differs: {state!r}",
    )

    encoded_namespace = quote(namespace, safe="")
    cluster_target = (
        "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
        f"{encoded_namespace}/clusters/{quote(cluster_name, safe='')}"
    )
    expected_transcript = [
        (
            "GET",
            f"/api/vcenter/namespaces/instances/v2/{encoded_namespace}",
            "getSupervisorNamespace",
        ),
        ("GET", cluster_target, "getVksCluster"),
        (
            "PATCH",
            f"/api/vcenter/namespaces/instances/{encoded_namespace}",
            "updateSupervisorNamespace",
        ),
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

    validate_request_headers(
        records,
        session_id=session_id,
        token=token,
    )
    for record in records[:2]:
        require(
            record["body_length"] == 0 and record["body_utf8"] == "",
            f"GET request {record['sequence']} was not bodyless",
        )

    namespace_body = json.dumps(
        {"description": namespace_description},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    cluster_body = json.dumps(
        {"spec": {"topology": {"version": target_version}}},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    require(
        records[2]["body_utf8"] == namespace_body
        and records[2]["body_length"] == len(namespace_body.encode("utf-8")),
        "vCenter UpdateSpec bytes differ",
    )
    require(
        records[3]["body_utf8"] == cluster_body
        and records[3]["body_length"] == len(cluster_body.encode("utf-8")),
        "Kubernetes merge patch bytes differ",
    )
    require(
        set(json.loads(records[2]["body_utf8"])) == {"description"},
        "unset vCenter UpdateSpec fields were serialized",
    )
    cluster_patch = json.loads(records[3]["body_utf8"])
    require(
        set(cluster_patch) == {"spec"}
        and set(cluster_patch["spec"]) == {"topology"}
        and set(cluster_patch["spec"]["topology"]) == {"version"},
        "unset Kubernetes patch fields were serialized",
    )

    origin = server.root_url
    constructor_cases = [
        (
            "blank vCenter origin",
            (" ", origin, session_id, token, 1.0),
        ),
        (
            "non-HTTP vCenter origin",
            ("ftp://example.test/", origin, session_id, token, 1.0),
        ),
        (
            "vCenter origin leading whitespace",
            (" " + origin, origin, session_id, token, 1.0),
        ),
        (
            "vCenter origin path",
            (origin + "/api", origin, session_id, token, 1.0),
        ),
        (
            "vCenter origin user information",
            (
                origin.replace("http://", "http://user@"),
                origin,
                session_id,
                token,
                1.0,
            ),
        ),
        (
            "vCenter origin query",
            (origin + "?mode=test", origin, session_id, token, 1.0),
        ),
        (
            "vCenter origin empty query",
            (origin + "?", origin, session_id, token, 1.0),
        ),
        (
            "Kubernetes origin fragment",
            (origin, origin + "#section", session_id, token, 1.0),
        ),
        (
            "Kubernetes origin empty fragment",
            (origin, origin + "#", session_id, token, 1.0),
        ),
        (
            "Kubernetes origin path",
            (origin, origin + "/apis", session_id, token, 1.0),
        ),
        (
            "blank vCenter credential",
            (origin, origin, " \t", token, 1.0),
        ),
        (
            "vCenter credential newline",
            (origin, origin, "bad\nsession", token, 1.0),
        ),
        (
            "blank Kubernetes credential",
            (origin, origin, session_id, "", 1.0),
        ),
        (
            "Kubernetes credential carriage return",
            (origin, origin, session_id, "bad\rtoken", 1.0),
        ),
        (
            "boolean timeout",
            (origin, origin, session_id, token, True),
        ),
        (
            "zero timeout",
            (origin, origin, session_id, token, 0),
        ),
        (
            "negative timeout",
            (origin, origin, session_id, token, -1.0),
        ),
        (
            "NaN timeout",
            (origin, origin, session_id, token, math.nan),
        ),
        (
            "infinite timeout",
            (origin, origin, session_id, token, math.inf),
        ),
        (
            "string timeout",
            (origin, origin, session_id, token, "1"),
        ),
    ]
    for label, arguments in constructor_cases:
        constructor_error = expect_raises(
            (TypeError, ValueError),
            lambda arguments=arguments: CoordinatedChangeClient(
                arguments[0],
                arguments[1],
                arguments[2],
                arguments[3],
                timeout=arguments[4],
            ),
            label,
        )
        message = str(constructor_error)
        for credential in arguments[2:4]:
            if isinstance(credential, str) and credential.strip():
                require(
                    credential not in message,
                    f"{label}: exception leaked a credential",
                )

    def run_response_case(
        overrides: dict[str, dict[str, Any]],
    ) -> tuple[
        dict[str, object] | None,
        Exception | None,
        list[dict[str, Any]],
        dict[str, Any],
    ]:
        with tempfile.TemporaryDirectory(
            prefix="vcf-vks-change-case-"
        ) as temporary:
            request_log = Path(temporary) / "requests.jsonl"
            case_server = ContractMockServer(
                ("127.0.0.1", 0),
                contract_path=ROOT / "docs" / "contract.json",
                request_log=request_log,
                namespace=namespace,
                supervisor=supervisor,
                cluster_name=cluster_name,
                cluster_class=cluster_class,
                old_description=old_description,
                old_version=old_version,
                failure_marker=failure_marker,
                response_overrides=overrides,
            )
            case_thread = threading.Thread(
                target=lambda: case_server.serve_forever(
                    poll_interval=0.01
                ),
                daemon=True,
            )
            case_thread.start()
            result: dict[str, object] | None = None
            caught: Exception | None = None
            try:
                case_client = CoordinatedChangeClient(
                    case_server.root_url,
                    case_server.root_url + "/",
                    session_id,
                    token,
                    timeout=5.0,
                )
                try:
                    result = case_client.coordinate_change(
                        supervisor=supervisor,
                        namespace=namespace,
                        cluster_name=cluster_name,
                        cluster_class=cluster_class,
                        namespace_description=namespace_description,
                        target_version=target_version,
                    )
                except Exception as error:
                    caught = error
            finally:
                case_server.shutdown()
                case_server.server_close()
                case_thread.join(timeout=5)
            return (
                result,
                caught,
                read_log(request_log),
                case_server.snapshot(),
            )

    def require_error_case(
        label: str,
        overrides: dict[str, dict[str, Any]],
        error_type: type[Exception],
        request_count: int,
    ) -> tuple[Exception, dict[str, Any]]:
        result, error, case_records, case_state = run_response_case(
            overrides
        )
        require(result is None, f"{label}: unexpectedly returned a ledger")
        require(
            isinstance(error, error_type),
            f"{label}: expected {error_type.__name__}, got "
            f"{type(error).__name__}",
        )
        require(
            len(case_records) == request_count,
            f"{label}: expected {request_count} requests, got "
            f"{len(case_records)}",
        )
        message = str(error)
        for secret in (session_id, token, failure_marker):
            require(
                secret not in message,
                f"{label}: exception leaked sensitive data",
            )
        return error, case_state

    namespace_info = {
        "supervisor": supervisor,
        "config_status": "RUNNING",
        "messages": [],
        "stats": {},
        "description": old_description,
        "access_list": [],
        "storage_specs": [],
    }
    cluster_info = {
        "apiVersion": "cluster.x-k8s.io/v1beta2",
        "kind": "Cluster",
        "metadata": {
            "name": cluster_name,
            "namespace": namespace,
        },
        "spec": {
            "topology": {
                "class": cluster_class,
                "version": old_version,
            }
        },
    }

    require_error_case(
        "vCenter redirect",
        {
            "getSupervisorNamespace": {
                "status": 302,
                "json": {"message": failure_marker},
                "headers": {"Location": "/unexpected"},
            }
        },
        RuntimeError,
        1,
    )
    require_error_case(
        "namespace malformed JSON",
        {
            "getSupervisorNamespace": {
                "status": 200,
                "raw": failure_marker.encode("utf-8"),
            }
        },
        RuntimeError,
        1,
    )
    require_error_case(
        "namespace non-object JSON",
        {
            "getSupervisorNamespace": {
                "status": 200,
                "json": [failure_marker],
            }
        },
        RuntimeError,
        1,
    )
    wrong_supervisor = dict(namespace_info)
    wrong_supervisor["supervisor"] = supervisor.upper()
    require_error_case(
        "namespace Supervisor mismatch",
        {
            "getSupervisorNamespace": {
                "status": 200,
                "json": wrong_supervisor,
            }
        },
        RuntimeError,
        1,
    )
    stopped_namespace = dict(namespace_info)
    stopped_namespace["config_status"] = "ERROR"
    require_error_case(
        "namespace not running",
        {
            "getSupervisorNamespace": {
                "status": 200,
                "json": stopped_namespace,
            }
        },
        RuntimeError,
        1,
    )
    require_error_case(
        "Cluster HTTP failure",
        {
            "getVksCluster": {
                "status": 404,
                "json": {"message": failure_marker},
            }
        },
        RuntimeError,
        2,
    )
    require_error_case(
        "Cluster malformed JSON",
        {
            "getVksCluster": {
                "status": 200,
                "raw": failure_marker.encode("utf-8"),
            }
        },
        RuntimeError,
        2,
    )

    cluster_variants: list[tuple[str, dict[str, Any]]] = []
    wrong_namespace = copy.deepcopy(cluster_info)
    wrong_namespace["metadata"]["namespace"] = namespace.upper()
    cluster_variants.append(("Cluster namespace mismatch", wrong_namespace))
    wrong_name = copy.deepcopy(cluster_info)
    wrong_name["metadata"]["name"] = cluster_name.upper()
    cluster_variants.append(("Cluster name mismatch", wrong_name))
    wrong_class = copy.deepcopy(cluster_info)
    wrong_class["spec"]["topology"]["class"] = cluster_class.upper()
    cluster_variants.append(("Cluster class mismatch", wrong_class))
    blank_version = copy.deepcopy(cluster_info)
    blank_version["spec"]["topology"]["version"] = " \t"
    cluster_variants.append(("Cluster blank current version", blank_version))
    nonstring_version = copy.deepcopy(cluster_info)
    nonstring_version["spec"]["topology"]["version"] = 123
    cluster_variants.append(
        ("Cluster non-string current version", nonstring_version)
    )
    for label, response in cluster_variants:
        require_error_case(
            label,
            {"getVksCluster": {"status": 200, "json": response}},
            RuntimeError,
            2,
        )

    _, failed_update_state = require_error_case(
        "namespace update HTTP failure",
        {
            "updateSupervisorNamespace": {
                "status": 500,
                "json": {"message": failure_marker},
            }
        },
        RuntimeError,
        3,
    )
    require(
        failed_update_state["namespace_description"] == old_description
        and failed_update_state["namespace_update_count"] == 0
        and failed_update_state["cluster_patch_attempts"] == 0,
        "failed namespace update crossed the commit boundary",
    )

    disconnect_error, _ = require_error_case(
        "pre-commit transport failure",
        {"getSupervisorNamespace": {"disconnect": True}},
        RuntimeError,
        1,
    )
    require(
        "remote end closed connection without response"
        not in str(disconnect_error).casefold(),
        "transport exception text leaked before the commit",
    )

    unknown_result, unknown_error, unknown_records, unknown_state = (
        run_response_case(
            {"patchVksClusterVersion": {"disconnect": True}}
        )
    )
    expected_unknown = {
        "overallStatus": "Failed",
        "namespaceChange": {
            "operationId": "Vcenter.Namespaces.Instances_update",
            "namespace": namespace,
            "status": "Succeeded",
            "changed": True,
        },
        "clusterChange": {
            "operationKey": (
                "cluster.x-k8s.io/v1beta2:"
                "namespaced-clusters:patch"
            ),
            "namespace": namespace,
            "name": cluster_name,
            "status": "Unknown",
            "changed": False,
            "httpStatus": None,
        },
    }
    require(
        unknown_error is None and unknown_result == expected_unknown,
        "post-commit transport ambiguity did not preserve the namespace ledger",
    )
    require(
        len(unknown_records) == 4,
        "post-commit transport ambiguity changed the operation count",
    )
    require(
        unknown_state
        == {
            "namespace_description": namespace_description,
            "cluster_version": old_version,
            "namespace_update_count": 1,
            "cluster_patch_attempts": 1,
        },
        "post-commit transport ambiguity changed committed state",
    )

    host = urlsplit(server.root_url).netloc
    require(host.startswith("127.0.0.1:"), "verifier did not use loopback")
    print("verification passed")


if __name__ == "__main__":
    main()
