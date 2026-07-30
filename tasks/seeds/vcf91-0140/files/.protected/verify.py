#!/usr/bin/env python3
"""Deterministic protected verifier for VKS Cluster inventory."""

from __future__ import annotations

import ast
import json
import math
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / ".protected" / "mock_api.py"
CLIENT_PATH = ROOT / "vcf_vks_inventory" / "client.py"

PINNED_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
PINNED_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
PINNED_SPEC = (
    "specifications/vsphere/openapi/automation/vcenter.yaml"
)
DISCOVERY_OPERATION = "Vcenter.Namespaces.User.Instances_list"
DISCOVERY_PATH = "/api/vcenter/namespaces-user/namespaces"
CLUSTER_OPERATION = (
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:list"
)
CLUSTER_PATH = (
    "/apis/cluster.x-k8s.io/v1beta2/"
    "namespaces/{namespace}/clusters"
)
UNSET_VCENTER_FIELDS = {"filter", "username", "groups"}
UNSET_KUBERNETES_FIELDS = {
    "pretty",
    "allowWatchBookmarks",
    "fieldSelector",
    "labelSelector",
    "resourceVersion",
    "resourceVersionMatch",
    "sendInitialEvents",
    "timeoutSeconds",
    "watch",
}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_contract() -> None:
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)
    source = contract["source"]

    require(
        source["repositoryCommitSha"] == PINNED_COMMIT,
        "contract repository commit changed",
    )
    require(
        source["specPath"] == PINNED_SPEC,
        "contract specification path changed",
    )
    require(
        source["specBlobSha"] == PINNED_BLOB,
        "contract specification blob changed",
    )
    require(
        source["license"] == "Apache-2.0",
        "contract source license changed",
    )
    require(
        source["openapi"] == "3.0.3"
        and source["apiVersion"] == "9.1.0.0",
        "contract must remain pinned to the VCF 9.1 vSphere spec",
    )
    require(
        source["serverBasePath"] == "/api",
        "vCenter server base path changed",
    )
    require(
        contract["securitySchemes"]["api_key_auth"]
        == {
            "type": "apiKey",
            "in": "header",
            "name": "vmware-api-session-id",
        },
        "vCenter authentication projection changed",
    )

    operations = contract["operations"]
    require(
        len(operations) == 1,
        "focused contract must name one VMware operation",
    )
    discovery = operations[0]
    require(
        discovery["operationId"] == DISCOVERY_OPERATION,
        "namespace discovery operationId changed",
    )
    require(
        (discovery["method"], discovery["wirePath"])
        == ("GET", DISCOVERY_PATH),
        "namespace discovery wire route changed",
    )
    require(
        discovery["specPathItem"]
        == "/vcenter/namespaces-user/namespaces",
        "namespace discovery specification path item changed",
    )
    require(
        discovery["requestBody"] is False,
        "namespace discovery must remain bodyless",
    )
    parameters = discovery["parameters"]
    require(
        [item["name"] for item in parameters]
        == ["filter", "groups"],
        "namespace discovery parameters changed",
    )
    require(
        all(
            item["required"] is False
            and item["omitWhenUnset"] is True
            for item in parameters
        ),
        "unset vCenter optionals must be omitted",
    )
    require(
        [item["name"] for item in discovery["effectiveQueryFields"]]
        == ["username", "groups"],
        "effective vCenter query projection changed",
    )
    summary = contract["schemas"][
        "Vcenter.Namespaces.User.Instances.Summary"
    ]
    require(
        summary["required"] == ["namespace", "master_host"],
        "namespace summary required fields changed",
    )
    require(
        contract["schemas"][
            "Vcenter.Namespaces.User.Instances.FilterSpec"
        ]["properties"]["username"]["omitWhenUnset"]
        is True,
        "filter.username omission rule changed",
    )

    kubernetes_operations = contract[
        "kubernetesIntegration"
    ]["operations"]
    require(
        len(kubernetes_operations) == 1,
        "focused contract must name one Kubernetes operation",
    )
    clusters = kubernetes_operations[0]
    require(
        clusters["operationKey"] == CLUSTER_OPERATION,
        "Kubernetes operation key changed",
    )
    require(
        (clusters["method"], clusters["pathTemplate"])
        == ("GET", CLUSTER_PATH),
        "Kubernetes Cluster collection route changed",
    )
    require(
        clusters["apiGroup"] == "cluster.x-k8s.io"
        and clusters["apiVersion"] == "v1beta2"
        and clusters["resource"] == "clusters"
        and clusters["scope"] == "namespaced"
        and clusters["verb"] == "list",
        "Kubernetes resource profile changed",
    )
    require(
        clusters["authentication"]
        == {
            "type": "http",
            "scheme": "bearer",
            "header": "Authorization",
        },
        "Kubernetes authentication projection changed",
    )
    query_names = [
        item["name"] for item in clusters["queryParameters"]
    ]
    require(
        query_names
        == [
            "limit",
            "continue",
            "pretty",
            "allowWatchBookmarks",
            "fieldSelector",
            "labelSelector",
            "resourceVersion",
            "resourceVersionMatch",
            "sendInitialEvents",
            "timeoutSeconds",
            "watch",
        ],
        "Kubernetes query projection changed",
    )
    require(
        set(query_names[2:]) == UNSET_KUBERNETES_FIELDS,
        "unset Kubernetes query fields changed",
    )
    require(
        "does not invent" in contract[
            "kubernetesIntegration"
        ]["provenanceNote"],
        "contract must distinguish Kubernetes from VMware operationIds",
    )

    require(
        sources["repositoryCommitSha"] == PINNED_COMMIT,
        "official source repository commit changed",
    )
    require(
        sources["specPath"] == PINNED_SPEC,
        "official source specification path changed",
    )
    require(
        sources["specBlobSha"] == PINNED_BLOB,
        "official source specification blob changed",
    )
    require(
        sources["license"] == "Apache-2.0",
        "official source license changed",
    )
    require(
        PINNED_COMMIT in sources["specUrl"]
        and sources["specUrl"].endswith(PINNED_SPEC),
        "official specification URL must pin commit and path",
    )
    require(
        sources["operationIds"] == [DISCOVERY_OPERATION],
        "official source must name the exact VMware operationId",
    )
    require(
        sources["operations"]
        == [
            {
                "operationId": DISCOVERY_OPERATION,
                "method": "GET",
                "path": "/vcenter/namespaces-user/namespaces",
                "specLine": 66261,
                "repositoryCommitSha": PINNED_COMMIT,
                "specPath": PINNED_SPEC,
                "usedFor": (
                    "list authorized vSphere Supervisor namespaces "
                    "and obtain the Kubernetes API master_host for "
                    "the selected namespace"
                ),
            }
        ],
        "official source operation record changed",
    )


def verify_client_source() -> None:
    source = CLIENT_PATH.read_text(encoding="utf-8")
    folded = source.casefold()
    tree = ast.parse(source, filename=str(CLIENT_PATH))

    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.split(".", 1)[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module:
                imported_roots.add(node.module.split(".", 1)[0])
    non_stdlib = imported_roots - sys.stdlib_module_names
    require(
        not non_stdlib,
        "client imports non-standard-library modules: "
        + ", ".join(sorted(non_stdlib)),
    )

    for forbidden in (
        ".protected",
        "mock_api",
        "official_sources",
        "request_log",
        "config.json",
        "subprocess",
    ):
        require(
            forbidden not in folded,
            f"client contains forbidden fixture marker: {forbidden}",
        )


def make_cluster(
    namespace: str,
    name: str,
    marker: str,
) -> dict[str, Any]:
    return {
        "apiVersion": "cluster.x-k8s.io/v1beta2",
        "kind": "Cluster",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "uid": str(uuid.uuid4()),
            "labels": {
                "verification.moonshiner.dev/run": marker,
            },
        },
        "spec": {
            "topology": {
                "class": "builtin-generic-v3.3.0",
                "version": "v1.33.1+vmware.1-fips-vkr.2",
            }
        },
        "status": {
            "phase": "Provisioned",
        },
    }


def wait_for_port(
    process: subprocess.Popen[str],
    port_path: Path,
) -> int:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise VerificationError(
                "mock exited before readiness "
                f"({process.returncode}): {stdout}\n{stderr}"
            )
        if port_path.exists():
            value = port_path.read_text(
                encoding="ascii"
            ).strip()
            if value:
                return int(value)
        time.sleep(0.02)
    raise VerificationError("timed out waiting for loopback mock")


def read_requests(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    lines = [
        line
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    return [json.loads(line) for line in lines]


def expect_rejected(
    action: Callable[[], Any],
    label: str,
) -> None:
    try:
        action()
    except (TypeError, ValueError):
        return
    except Exception as exc:
        raise VerificationError(
            f"{label} raised the wrong exception: "
            f"{type(exc).__name__}"
        ) from exc
    raise VerificationError(f"{label} was accepted")


def verify_input_validation(
    client_type: type,
    client: Any,
    log_path: Path,
    session_token: str,
    kubernetes_token: str,
    port: int,
) -> None:
    good_url = f"http://127.0.0.1:{port}"
    invalid_urls = [
        "",
        "ftp://127.0.0.1",
        f"http://user@127.0.0.1:{port}",
        f"http://127.0.0.1:{port}/api",
        f"http://127.0.0.1:{port}?query=1",
        f"http://127.0.0.1:{port}#fragment",
        "http://127.0.0.1:99999",
    ]
    for value in invalid_urls:
        expect_rejected(
            lambda value=value: client_type(
                value,
                session_token,
                kubernetes_token,
            ),
            f"invalid vcenter_url {value!r}",
        )

    for value in ["", "   ", "bad\r\nheader"]:
        expect_rejected(
            lambda value=value: client_type(
                good_url,
                value,
                kubernetes_token,
            ),
            "invalid vCenter session id",
        )
        expect_rejected(
            lambda value=value: client_type(
                good_url,
                session_token,
                value,
            ),
            "invalid Kubernetes token",
        )

    for value in [True, 0, -1, math.inf, -math.inf, math.nan]:
        expect_rejected(
            lambda value=value: client_type(
                good_url,
                session_token,
                kubernetes_token,
                timeout=value,
            ),
            f"invalid timeout {value!r}",
        )
    for value in ["", "HTTP", "ftp"]:
        expect_rejected(
            lambda value=value: client_type(
                good_url,
                session_token,
                kubernetes_token,
                kubernetes_scheme=value,
            ),
            f"invalid Kubernetes scheme {value!r}",
        )

    for value in ["", "   ", None, 7]:
        expect_rejected(
            lambda value=value: client.list_clusters(value),
            f"invalid namespace {value!r}",
        )
    for value in [True, 0, -1, 2**63]:
        expect_rejected(
            lambda value=value: client.list_clusters(
                "unused-valid-namespace",
                page_size=value,
            ),
            f"invalid page size {value!r}",
        )
    require(
        read_requests(log_path) == [],
        "construction and invalid inputs must perform no request",
    )


def expected_headers(
    authority: str,
    authentication_name: str,
    authentication_value: str,
) -> list[dict[str, str]]:
    return [
        {"name": "Host", "value": authority},
        {"name": "Accept", "value": "application/json"},
        {
            "name": authentication_name,
            "value": authentication_value,
        },
    ]


def verify_wire(
    requests: list[dict[str, Any]],
    port: int,
    namespace: str,
    page_size: int,
    markers: list[str],
    session_token: str,
    kubernetes_token: str,
) -> None:
    require(
        len(requests) == 8,
        "expected two complete four-request inventory calls, got "
        f"{len(requests)} requests",
    )
    authority = f"127.0.0.1:{port}"
    encoded_namespace = quote(namespace, safe="")
    cluster_path = CLUSTER_PATH.replace(
        "{namespace}",
        encoded_namespace,
    )
    cluster_targets = [
        f"{cluster_path}?limit={page_size}",
        (
            f"{cluster_path}?limit={page_size}&continue="
            f"{quote(markers[0], safe='')}"
        ),
        (
            f"{cluster_path}?limit={page_size}&continue="
            f"{quote(markers[1], safe='')}"
        ),
    ]

    for run in range(2):
        offset = run * 4
        discovery = requests[offset]
        require(
            discovery["operation"] == DISCOVERY_OPERATION,
            f"run {run + 1} did not start with namespace discovery",
        )
        require(
            discovery["method"] == "GET"
            and discovery["raw_target"] == DISCOVERY_PATH,
            "namespace discovery method or raw target changed",
        )
        require(
            discovery["query_pairs"] == [],
            "namespace discovery sent an optional query field",
        )
        require(
            discovery["headers"]
            == expected_headers(
                authority,
                "vmware-api-session-id",
                session_token,
            ),
            "namespace discovery header wire shape changed",
        )
        require(
            discovery["body_length"] == 0
            and discovery["body_utf8"] == "",
            "namespace discovery request must have a zero-byte body",
        )

        for page_number, expected_target in enumerate(
            cluster_targets
        ):
            request = requests[offset + page_number + 1]
            require(
                request["operation"] == CLUSTER_OPERATION,
                "request escaped the focused Kubernetes operation",
            )
            require(
                request["method"] == "GET"
                and request["raw_target"] == expected_target,
                "Kubernetes method, raw target, query order, or "
                "escaping changed on page "
                f"{page_number + 1}",
            )
            expected_pairs = [("limit", str(page_size))]
            if page_number:
                expected_pairs.append(
                    ("continue", markers[page_number - 1])
                )
            require(
                request["query_pairs"]
                == [list(pair) for pair in expected_pairs],
                "Kubernetes query decoding does not match continuity",
            )
            require(
                request["headers"]
                == expected_headers(
                    authority,
                    "Authorization",
                    "Bearer " + kubernetes_token,
                ),
                "Kubernetes header wire shape changed",
            )
            require(
                request["body_length"] == 0
                and request["body_utf8"] == "",
                "Kubernetes list request must have a zero-byte body",
            )

    serialized_query_names = {
        pair[0]
        for request in requests
        for pair in request["query_pairs"]
    }
    for field in UNSET_VCENTER_FIELDS | UNSET_KUBERNETES_FIELDS:
        require(
            field not in serialized_query_names,
            f"unset optional field was serialized: {field}",
        )


def verify_fault_wire(
    requests: list[dict[str, Any]],
    port: int,
    namespace: str,
    page_size: int,
    session_token: str,
    kubernetes_token: str,
) -> None:
    require(
        len(requests) == 10,
        "malformed first page must stop the third inventory call "
        f"after two requests, got {len(requests) - 8}",
    )
    authority = f"127.0.0.1:{port}"
    discovery = requests[8]
    require(
        discovery["operation"] == DISCOVERY_OPERATION
        and discovery["method"] == "GET"
        and discovery["raw_target"] == DISCOVERY_PATH
        and discovery["query_pairs"] == []
        and discovery["headers"]
        == expected_headers(
            authority,
            "vmware-api-session-id",
            session_token,
        )
        and discovery["body_length"] == 0,
        "fault probe changed the vCenter request wire shape",
    )
    cluster_path = CLUSTER_PATH.replace(
        "{namespace}",
        quote(namespace, safe=""),
    )
    first_page = requests[9]
    require(
        first_page["operation"] == CLUSTER_OPERATION
        and first_page["method"] == "GET"
        and first_page["raw_target"]
        == f"{cluster_path}?limit={page_size}"
        and first_page["query_pairs"]
        == [["limit", str(page_size)]]
        and first_page["headers"]
        == expected_headers(
            authority,
            "Authorization",
            "Bearer " + kubernetes_token,
        )
        and first_page["body_length"] == 0,
        "fault probe changed the Kubernetes request wire shape",
    )


def verify_error_surfaces(
    error_type: type,
    protocol_type: type,
    session_token: str,
    kubernetes_token: str,
) -> None:
    payload = {
        "message": "body-must-not-appear",
        "credential": kubernetes_token,
    }
    error = error_type(DISCOVERY_OPERATION, 503, payload)
    require(
        error.operation_id == DISCOVERY_OPERATION
        and error.status_code == 503
        and error.payload is payload,
        "VksInventoryError fields changed",
    )
    rendered = str(error) + repr(error)
    for secret in (
        session_token,
        kubernetes_token,
        "body-must-not-appear",
    ):
        require(
            secret not in rendered,
            "VksInventoryError text or repr leaked protected data",
        )

    protocol = protocol_type(CLUSTER_OPERATION, "invalid page")
    require(
        protocol.operation_id == CLUSTER_OPERATION,
        "ProtocolError operation_id changed",
    )


def run_verification() -> None:
    verify_contract()
    verify_client_source()

    sys.path.insert(0, str(ROOT))
    from vcf_vks_inventory import (  # noqa: PLC0415
        ProtocolError,
        VksClusterInventoryClient,
        VksInventoryError,
    )

    nonce = secrets.token_hex(6)
    namespace = f"team-{nonce}"
    distractor = f"other-{secrets.token_hex(6)}"
    session_token = "vc-" + secrets.token_urlsafe(30)
    kubernetes_token = "k8s-" + secrets.token_urlsafe(34)
    page_size = 137
    markers = [
        "next/one+ =%雪~" + secrets.token_urlsafe(7),
        "next/two?&=+#" + secrets.token_urlsafe(7),
    ]
    marker = "run-" + secrets.token_hex(5)
    names = [
        f"zeta-{nonce}",
        f"alpha-{nonce}",
        f"middle-{nonce}",
        f"cluster-2-{nonce}",
        f"cluster-10-{nonce}",
        f"beta-{nonce}",
        f"omega-{nonce}",
    ]
    clusters = [
        make_cluster(namespace, name, marker) for name in names
    ]
    pages = [
        [clusters[0], clusters[3], clusters[1]],
        [clusters[6], clusters[2]],
        [clusters[4], clusters[5]],
    ]
    expected = sorted(
        clusters,
        key=lambda item: (
            item["metadata"]["name"],
            item["metadata"]["uid"],
            json.dumps(
                item,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
        ),
    )

    with tempfile.TemporaryDirectory(
        prefix="moonshiner-vks-"
    ) as temporary:
        temp_root = Path(temporary)
        config_path = temp_root / "config.json"
        log_path = temp_root / "requests.jsonl"
        port_path = temp_root / "port.txt"
        config_path.write_text(
            json.dumps(
                {
                    "namespace": namespace,
                    "distractor_namespace": distractor,
                    "vcenter_session_id": session_token,
                    "kubernetes_token": kubernetes_token,
                    "page_size": page_size,
                    "markers": markers,
                    "pages": pages,
                    "resource_version": secrets.token_hex(12),
                    "fault": "wrong_item_namespace",
                    "fault_collection": 2,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK_PATH),
                "--contract",
                str(CONTRACT_PATH),
                "--config",
                str(config_path),
                "--log",
                str(log_path),
                "--port-file",
                str(port_path),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            port = wait_for_port(process, port_path)
            client = VksClusterInventoryClient(
                f"http://127.0.0.1:{port}",
                session_token,
                kubernetes_token,
                timeout=5.0,
                kubernetes_scheme="http",
            )
            verify_input_validation(
                VksClusterInventoryClient,
                client,
                log_path,
                session_token,
                kubernetes_token,
                port,
            )
            verify_error_surfaces(
                VksInventoryError,
                ProtocolError,
                session_token,
                kubernetes_token,
            )

            first = client.list_clusters(
                namespace,
                page_size=page_size,
            )
            second = client.list_clusters(
                namespace,
                page_size=page_size,
            )
            require(
                first == expected,
                "first call did not return every intact Cluster in "
                "stable ordinal order",
            )
            require(
                second == expected,
                "reversed service order changed inventory output",
            )
            require(
                first is not second,
                "each inventory call must return a new list",
            )
            require(
                len(first) == sum(len(page) for page in pages),
                "one or more pagination pages were not collected",
            )

            requests = read_requests(log_path)
            verify_wire(
                requests,
                port,
                namespace,
                page_size,
                markers,
                session_token,
                kubernetes_token,
            )

            try:
                client.list_clusters(
                    namespace,
                    page_size=page_size,
                )
            except ProtocolError as exc:
                require(
                    exc.operation_id == CLUSTER_OPERATION,
                    "malformed Cluster used the wrong ProtocolError "
                    "operation",
                )
            except Exception as exc:
                raise VerificationError(
                    "malformed Cluster page raised the wrong "
                    f"exception: {type(exc).__name__}"
                ) from exc
            else:
                raise VerificationError(
                    "malformed Cluster page returned partial data"
                )
            verify_fault_wire(
                read_requests(log_path),
                port,
                namespace,
                page_size,
                session_token,
                kubernetes_token,
            )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            stdout, stderr = process.communicate()
            require(
                process.returncode in {0, -15},
                "mock exited unexpectedly "
                f"({process.returncode}): {stdout}\n{stderr}",
            )


def main() -> int:
    try:
        run_verification()
    except Exception as exc:
        print(
            f"verification failed: {exc}",
            file=sys.stderr,
        )
        return 1
    print("verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
