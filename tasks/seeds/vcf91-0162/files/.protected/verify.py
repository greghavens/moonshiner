"""Deterministic protected verifier for the Java VCF/VKS rotation task."""

from __future__ import annotations

import base64
import errno
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from contract_mock import ContractMockServer  # noqa: E402


COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_BLOB = "8028b0824c4ff3503d05f44814f967938a795c40"
SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
VCENTER_IDS = [
    "Cis.Session_create",
    "Vcenter.Namespaces.User.Instances_list",
    "Cis.Session_delete",
]
CONTRACT_NAMES = [
    "createVcenterSession",
    "listSupervisorNamespaces",
    "getVksCluster",
    "deleteVcenterSession",
]
KUBERNETES_KEY = (
    "cluster.x-k8s.io/v1beta2:namespaced-clusters:get"
)


def fail(message: str) -> None:
    raise AssertionError(message)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


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
        contract.get("securitySchemes")
        == {
            "basic_auth": {
                "type": "http",
                "scheme": "basic",
            },
            "api_key_auth": {
                "type": "apiKey",
                "in": "header",
                "name": "vmware-api-session-id",
            },
        },
        "security-scheme projection changed",
    )

    operations = contract.get("operations")
    require(isinstance(operations, list), "contract operations are missing")
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
            (
                item.get("method"),
                item.get("specPathItem"),
                item.get("pathTemplate"),
            )
            for item in vcenter
        ]
        == [
            ("POST", "/session", "/api/session"),
            (
                "GET",
                "/vcenter/namespaces-user/namespaces",
                "/api/vcenter/namespaces-user/namespaces",
            ),
            ("DELETE", "/session", "/api/session"),
        ],
        "vCenter methods or paths changed",
    )
    require(
        vcenter[0].get("requestBody") is False
        and vcenter[0].get("queryParameters") == []
        and vcenter[0].get("security") == ["basic_auth"]
        and vcenter[0]["responses"]["201"]["schema"]
        == {"type": "string", "format": "password"},
        "session-create projection changed",
    )
    require(
        vcenter[2].get("requestBody") is False
        and vcenter[2].get("queryParameters") == []
        and vcenter[2].get("security") == ["api_key_auth"]
        and vcenter[2]["responses"]["204"] == {"body": False},
        "session-delete projection changed",
    )

    list_operation = vcenter[1]
    require(
        [
            (
                item.get("name"),
                item.get("required"),
                item.get("omitWhenUnset"),
            )
            for item in list_operation.get("queryParameters", [])
        ]
        == [
            ("filter", False, True),
            ("groups", False, True),
        ],
        "namespace-list optional query projection changed",
    )
    schemas = contract.get("schemas", {})
    require(
        schemas.get("Vcenter.Namespaces.User.Instances.FilterSpec")
        == {
            "type": "object",
            "required": [],
            "properties": {
                "username": {
                    "type": "string",
                    "required": False,
                    "omitWhenUnset": True,
                }
            },
        },
        "namespace filter projection changed",
    )
    summary = schemas.get(
        "Vcenter.Namespaces.User.Instances.Summary",
        {},
    )
    require(
        summary.get("required") == ["namespace", "master_host"]
        and set(summary.get("properties", {}))
        == {"namespace", "master_host"},
        "namespace summary projection changed",
    )

    kubernetes = [
        item
        for item in operations
        if item.get("sourceKind") == "supervisor-kubernetes-resource"
    ]
    require(len(kubernetes) == 1, "Kubernetes allow-list changed")
    require(
        kubernetes[0].get("operationKey") == KUBERNETES_KEY
        and kubernetes[0].get("method") == "GET"
        and kubernetes[0].get("optionalQueryFields") == ["pretty"]
        and kubernetes[0].get("successStatuses") == [200],
        "Kubernetes operation projection changed",
    )
    require(
        "operationId" not in kubernetes[0],
        "Kubernetes route claims a fictional VMware operationId",
    )
    require(
        "not represented as a VMware operationId"
        in contract.get("kubernetesProvenanceNote", ""),
        "Kubernetes provenance note changed",
    )
    require(
        contract.get("sessionLifecycleProjection")
        == {
            "createExchangesCredentialsForSessionToken": True,
            "deleteInvalidatesSessionToken": True,
            "operationsAlreadyInProgressContinueToCompletion": True,
        },
        "session lifecycle projection changed",
    )

    require(
        sources.get("repositoryCommitSha") == COMMIT
        and sources.get("specBlobSha") == SPEC_BLOB
        and sources.get("specPath") == SPEC_PATH,
        "official source pin changed",
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


def validate_java_shape() -> None:
    source_dir = ROOT / "src"
    sources = sorted(source_dir.glob("*.java"))
    require(
        [path.name for path in sources]
        == ["VcfVksSessionRotationClient.java"],
        "exactly one production Java source is required",
    )
    source = sources[0].read_text(encoding="utf-8")
    folded = source.casefold()
    require("package " not in folded, "the production source must be standalone")
    require(
        "unsupportedoperationexception" not in folded
        and "todo" not in folded,
        "client implementation is incomplete",
    )
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            require(
                stripped.startswith("import java."),
                f"non-standard-library import found: {stripped}",
            )
    for forbidden in (
        "processbuilder",
        "runtime.getruntime",
        "java.lang.reflect",
        "socketchannel",
        "serversocket",
        "datagramsocket",
        "system.setproperty",
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


def run_java_case(
    *,
    mode: str,
    endpoint: str,
    old_session: str,
    kubernetes_token: str,
    username: str,
    password: str,
    namespace: str,
    cluster_name: str,
    topology_version: str,
    request_log: Path,
    contract_path: Path,
    classes: Path,
    new_session: str,
    basic_value: str,
) -> subprocess.CompletedProcess[str]:
    compile_result = subprocess.run(
        [
            "javac",
            "--release",
            "17",
            "-encoding",
            "UTF-8",
            "-d",
            str(classes),
            str(ROOT / "src" / "VcfVksSessionRotationClient.java"),
            str(ROOT / "tests" / "TestMain.java"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    require(
        compile_result.returncode == 0,
        "javac failed:\n" + compile_result.stdout + compile_result.stderr,
    )
    environment = dict(os.environ)
    environment["VCF_TEST_NEW_SESSION"] = new_session
    environment["VCF_TEST_BASIC"] = basic_value
    return subprocess.run(
        [
            "java",
            "-cp",
            str(classes),
            "TestMain",
            mode,
            endpoint,
            old_session,
            kubernetes_token,
            username,
            password,
            namespace,
            cluster_name,
            topology_version,
            str(request_log),
            str(contract_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=environment,
        timeout=20,
        check=False,
    )


def main() -> None:
    validate_source_contract()
    validate_java_shape()

    suffix = secrets.token_hex(7)
    namespace = f"team blue/ñ-{suffix}"
    cluster_name = f"payments #1/{suffix}"
    topology_version = f"v1.33.{secrets.randbelow(8) + 1}+vmware.2"
    old_session = secrets.token_urlsafe(29)
    new_session = secrets.token_urlsafe(31)
    kubernetes_token = secrets.token_urlsafe(37)
    username = f"rotation-{secrets.token_hex(8)}"
    password = secrets.token_urlsafe(28)
    basic_value = "Basic " + base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("ascii")

    with tempfile.TemporaryDirectory(
        prefix="vcf-vks-java-rotation-"
    ) as temporary:
        temporary_path = Path(temporary)
        request_log = temporary_path / "requests.jsonl"
        classes = temporary_path / "classes"
        classes.mkdir()
        server: ContractMockServer | None
        server_thread: threading.Thread | None
        mode = "loopback"
        try:
            server = ContractMockServer(
                ("127.0.0.1", 0),
                contract_path=ROOT / "docs" / "contract.json",
                request_log=request_log,
                old_session=old_session,
                new_session=new_session,
                expected_basic=basic_value,
                kubernetes_token=kubernetes_token,
                namespace=namespace,
                cluster_name=cluster_name,
                topology_version=topology_version,
            )
        except OSError as error:
            if error.errno not in {errno.EPERM, errno.EACCES}:
                raise
            server = None
            mode = "fallback"
        if server is None:
            server_thread = None
            endpoint = "http://127.0.0.1:1"
        else:
            endpoint = server.root_url
            server_thread = threading.Thread(
                target=server.serve_forever,
                daemon=True,
            )
            server_thread.start()
        try:
            java_result = run_java_case(
                mode=mode,
                endpoint=endpoint,
                old_session=old_session,
                kubernetes_token=kubernetes_token,
                username=username,
                password=password,
                namespace=namespace,
                cluster_name=cluster_name,
                topology_version=topology_version,
                request_log=request_log,
                contract_path=ROOT / "docs" / "contract.json",
                classes=classes,
                new_session=new_session,
                basic_value=basic_value,
            )
        finally:
            if server is not None:
                server.new_cluster_response_sent.set()
                server.shutdown()
                server.server_close()
                assert server_thread is not None
                server_thread.join(timeout=5)

        records = read_log(request_log)
        state = server.snapshot() if server is not None else {
            "create_count": 1,
            "old_namespace_count": 1,
            "new_namespace_count": 1,
            "cluster_get_count": 2,
            "delete_count": 1,
            "delete_before_drain": False,
            "deleted_old_session": True,
        }

    combined_output = java_result.stdout + java_result.stderr
    for secret in (
        old_session,
        new_session,
        kubernetes_token,
        username,
        password,
    ):
        require(
            secret not in combined_output,
            "Java output disclosed sensitive material",
        )
    require(
        java_result.returncode == 0,
        "TestMain failed:\n" + combined_output,
    )
    require(
        java_result.stdout.strip() == "TEST_MAIN_OK"
        and java_result.stderr == "",
        "TestMain emitted unexpected output",
    )

    require(
        state
        == {
            "create_count": 1,
            "old_namespace_count": 1,
            "new_namespace_count": 1,
            "cluster_get_count": 2,
            "delete_count": 1,
            "delete_before_drain": False,
            "deleted_old_session": True,
        },
        f"rotation/drain state differs: {state!r}",
    )

    encoded_namespace = quote(namespace, safe="")
    encoded_cluster = quote(cluster_name, safe="")
    namespace_target = "/api/vcenter/namespaces-user/namespaces"
    cluster_target = (
        "/apis/cluster.x-k8s.io/v1beta2/namespaces/"
        f"{encoded_namespace}/clusters/{encoded_cluster}"
    )
    expected_transcript = [
        ("GET", namespace_target, "listSupervisorNamespaces"),
        ("POST", "/api/session", "createVcenterSession"),
        ("GET", namespace_target, "listSupervisorNamespaces"),
        ("GET", cluster_target, "getVksCluster"),
        ("GET", cluster_target, "getVksCluster"),
        ("DELETE", "/api/session", "deleteVcenterSession"),
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
    require(
        all(
            record["body_length"] == 0
            and record["body_utf8"] == ""
            for record in records
        ),
        "a contract-bodyless request sent entity bytes",
    )
    if mode == "fallback":
        require(
            [
                (
                    record.get("publisher_present"),
                    record.get("publisher_length"),
                )
                for record in records
            ]
            == [
                (False, -1),
                (True, 0),
                (False, -1),
                (False, -1),
                (False, -1),
                (False, -1),
            ],
            "request body-publisher shape differs",
        )

    headers = [header_values(record) for record in records]
    if mode == "loopback":
        expected_header_sets = [
            {"host", "user-agent", "accept", "vmware-api-session-id"},
            {
                "host",
                "user-agent",
                "accept",
                "authorization",
                "content-length",
            },
            {"host", "user-agent", "accept", "vmware-api-session-id"},
            {"host", "user-agent", "accept", "authorization"},
            {"host", "user-agent", "accept", "authorization"},
            {"host", "user-agent", "accept", "vmware-api-session-id"},
        ]
    else:
        expected_header_sets = [
            {"accept", "vmware-api-session-id"},
            {"accept", "authorization"},
            {"accept", "vmware-api-session-id"},
            {"accept", "authorization"},
            {"accept", "authorization"},
            {"accept", "vmware-api-session-id"},
        ]
    for record, actual, expected in zip(
        records,
        headers,
        expected_header_sets,
        strict=True,
    ):
        require(
            set(actual) == expected,
            f"request {record['sequence']} header set differs: "
            f"{set(actual)!r}",
        )
        require(
            actual.get("accept") == ["application/json"],
            f"request {record['sequence']} has the wrong Accept",
        )
        require(
            "content-type" not in actual
            and "transfer-encoding" not in actual
            and "content-encoding" not in actual,
            f"request {record['sequence']} sent entity metadata",
        )

    expected_host = urlsplit(endpoint).netloc
    if mode == "loopback":
        require(
            all(actual.get("host") == [expected_host] for actual in headers),
            "a request targeted a non-loopback authority",
        )
    else:
        require(
            all(
                record.get("authority") == expected_host
                for record in records
            ),
            "a fallback request targeted a non-loopback authority",
        )
    require(
        headers[0]["vmware-api-session-id"] == [old_session],
        "old namespace request did not use the old session",
    )
    require(
        headers[1]["authorization"] == [basic_value]
        and (
            headers[1].get("content-length") == ["0"]
            if mode == "loopback"
            else (
                records[1].get("publisher_present") is True
                and records[1].get("publisher_length") == 0
            )
        ),
        "session creation authentication or framing differs",
    )
    require(
        headers[2]["vmware-api-session-id"] == [new_session],
        "new namespace request did not use the published session",
    )
    require(
        headers[3]["authorization"] == [f"Bearer {kubernetes_token}"]
        and headers[4]["authorization"] == [f"Bearer {kubernetes_token}"],
        "Kubernetes bearer authentication differs",
    )
    require(
        headers[5]["vmware-api-session-id"] == [old_session],
        "session deletion did not target the drained old secret",
    )
    for index, actual in enumerate(headers):
        if index != 1:
            require(
                basic_value not in actual.get("authorization", []),
                "Basic credentials crossed an operation boundary",
            )
        if index not in {3, 4}:
            require(
                f"Bearer {kubernetes_token}"
                not in actual.get("authorization", []),
                "Kubernetes credentials crossed an operation boundary",
            )
    require(
        urlsplit(endpoint).hostname == "127.0.0.1",
        "verifier did not use IPv4 loopback",
    )
    print("verification passed")


if __name__ == "__main__":
    main()
