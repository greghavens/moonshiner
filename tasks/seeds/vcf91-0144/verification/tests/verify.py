"""Deterministic protected verifier for the VCF/VKS rotation task."""

from __future__ import annotations

import ast
import base64
import json
import math
import secrets
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mock_api import ContractMockServer  # noqa: E402
from vcf_vks_rotation import (  # noqa: E402
    ApiError,
    ProtocolError,
    RotatingVksClient,
)


COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
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
        and vcenter[0].get("security") == ["basic_auth"]
        and vcenter[0]["responses"]["201"]["schema"]
        == {"type": "string", "format": "password"},
        "session-create projection changed",
    )
    require(
        vcenter[2].get("requestBody") is False
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
        and kubernetes[0].get("optionalQueryFields") == ["pretty"],
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
    lifecycle = contract.get("sessionLifecycleProjection", {})
    require(
        lifecycle
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


def validate_python_shape() -> None:
    require(
        sys.version_info >= (3, 11),
        "verification requires Python 3.11 or later",
    )
    path = ROOT / "vcf_vks_rotation" / "client.py"
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
        "requests.",
    ):
        require(
            forbidden not in folded,
            f"forbidden implementation mechanism found: {forbidden}",
        )
    require(
        issubclass(ApiError, RuntimeError)
        and issubclass(ProtocolError, RuntimeError),
        "public error types changed",
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


def wait_for_generation(
    client: RotatingVksClient,
    generation: int,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if client.session_generation == generation:
            return
        time.sleep(0.01)
    fail(f"session generation {generation} was not published")


def validate_malformed_master_host(
    *,
    old_session: str,
    new_session: str,
    kubernetes_token: str,
    basic_value: str,
    namespace: str,
    cluster_name: str,
    topology_version: str,
) -> None:
    with tempfile.TemporaryDirectory(
        prefix="vcf-vks-malformed-host-"
    ) as temporary:
        request_log = Path(temporary) / "requests.jsonl"
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
            master_host_override="[::1",
        )
        server_thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
        )
        server_thread.start()
        try:
            client = RotatingVksClient(
                server.root_url,
                new_session,
                kubernetes_token,
                kubernetes_scheme="http",
                timeout=5.0,
            )
            try:
                client.get_cluster(namespace, cluster_name)
            except ProtocolError as error:
                require(
                    error.operation_id
                    == "Vcenter.Namespaces.User.Instances_list",
                    "malformed master_host used the wrong operation id",
                )
            except Exception as error:
                fail(
                    "malformed master_host raised unexpected "
                    f"{type(error).__name__}"
                )
            else:
                fail("malformed master_host did not raise ProtocolError")
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)

        records = read_log(request_log)
        require(
            [
                (
                    record["method"],
                    record["raw_target"],
                    record["operation"],
                )
                for record in records
            ]
            == [
                (
                    "GET",
                    "/api/vcenter/namespaces-user/namespaces",
                    "listSupervisorNamespaces",
                )
            ],
            "malformed master_host triggered unexpected traffic",
        )


def main() -> None:
    validate_source_contract()
    validate_python_shape()

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
        prefix="vcf-vks-rotation-"
    ) as temporary:
        request_log = Path(temporary) / "requests.jsonl"
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
        server_thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
        )
        server_thread.start()
        outcomes: dict[str, object] = {}
        errors: dict[str, BaseException] = {}

        def capture(label: str, call: Callable[[], object]) -> None:
            try:
                outcomes[label] = call()
            except BaseException as error:
                errors[label] = error

        try:
            client = RotatingVksClient(
                server.root_url + "/",
                old_session,
                kubernetes_token,
                kubernetes_scheme="http",
                timeout=5.0,
            )
            require(
                client.session_generation == 0,
                "initial session generation is not zero",
            )
            require(
                read_log(request_log) == [],
                "client construction or property access performed traffic",
            )
            expect_raises(
                (TypeError, ValueError),
                lambda: client.get_cluster(namespace, " "),
                "blank Cluster name",
            )
            expect_raises(
                (TypeError, ValueError),
                lambda: client.rotate_vcenter_session(
                    "invalid:user",
                    password,
                ),
                "ambiguous Basic username",
            )
            require(
                read_log(request_log) == [],
                "input validation was not completed before traffic",
            )

            old_thread = threading.Thread(
                target=capture,
                args=(
                    "old",
                    lambda: client.get_cluster(namespace, cluster_name),
                ),
                daemon=True,
            )
            old_thread.start()
            require(
                server.old_namespace_started.wait(timeout=3),
                "old-generation namespace request did not start",
            )

            rotation_thread = threading.Thread(
                target=capture,
                args=(
                    "rotation",
                    lambda: client.rotate_vcenter_session(
                        username,
                        password,
                    ),
                ),
                daemon=True,
            )
            rotation_thread.start()
            wait_for_generation(client, 1, 3)
            require(
                rotation_thread.is_alive(),
                "rotation returned before the old generation drained",
            )

            new_thread = threading.Thread(
                target=capture,
                args=(
                    "new",
                    lambda: client.get_cluster(namespace, cluster_name),
                ),
                daemon=True,
            )
            new_thread.start()
            new_thread.join(timeout=5)
            require(
                not new_thread.is_alive(),
                "new-generation work was blocked behind the old lease",
            )
            old_thread.join(timeout=5)
            rotation_thread.join(timeout=5)
            require(
                not old_thread.is_alive(),
                "old-generation lookup was stranded",
            )
            require(
                not rotation_thread.is_alive(),
                "rotation did not finish after the drain",
            )
        finally:
            server.new_cluster_seen.set()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)

        records = read_log(request_log)
        state = server.snapshot()

    if errors:
        rendered_errors = {
            label: f"{type(error).__name__}: {error!s} / {error!r}"
            for label, error in errors.items()
        }
        for secret in (
            old_session,
            new_session,
            kubernetes_token,
            username,
            password,
        ):
            require(
                secret not in repr(rendered_errors),
                "an error disclosed sensitive material",
            )
        fail(f"concurrent workflow raised: {rendered_errors!r}")

    expected_common = {
        "operationId": "Vcenter.Namespaces.User.Instances_list",
        "operationKey": KUBERNETES_KEY,
        "namespace": namespace,
        "name": cluster_name,
        "topologyVersion": topology_version,
    }
    require(
        outcomes.get("old")
        == {
            **expected_common,
            "sessionGeneration": 0,
        },
        f"old-generation result differs: {outcomes.get('old')!r}",
    )
    require(
        outcomes.get("new")
        == {
            **expected_common,
            "sessionGeneration": 1,
        },
        f"new-generation result differs: {outcomes.get('new')!r}",
    )
    require(
        outcomes.get("rotation") == 1,
        f"rotation returned the wrong generation: {outcomes.get('rotation')!r}",
    )
    require(
        client.session_generation == 1,
        "published generation changed after rotation",
    )
    rendered_outcomes = json.dumps(
        outcomes,
        ensure_ascii=False,
        sort_keys=True,
    )
    for secret in (
        old_session,
        new_session,
        kubernetes_token,
        username,
        password,
    ):
        require(
            secret not in rendered_outcomes,
            "returned data disclosed sensitive material",
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
        (
            "GET",
            namespace_target,
            "listSupervisorNamespaces",
        ),
        ("POST", "/api/session", "createVcenterSession"),
        (
            "GET",
            namespace_target,
            "listSupervisorNamespaces",
        ),
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

    expected_header_sets = [
        {
            "host",
            "accept",
            "vmware-api-session-id",
        },
        {
            "host",
            "accept",
            "authorization",
            "content-length",
        },
        {
            "host",
            "accept",
            "vmware-api-session-id",
        },
        {
            "host",
            "accept",
            "authorization",
        },
        {
            "host",
            "accept",
            "authorization",
        },
        {
            "host",
            "accept",
            "vmware-api-session-id",
        },
    ]
    headers = [header_values(record) for record in records]
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
            and "transfer-encoding" not in actual,
            f"request {record['sequence']} sent entity metadata",
        )

    require(
        headers[0]["vmware-api-session-id"] == [old_session],
        "old namespace request did not use the old session",
    )
    require(
        headers[1]["authorization"] == [basic_value]
        and headers[1]["content-length"] == ["0"],
        "session creation wire authentication/framing differs",
    )
    require(
        headers[2]["vmware-api-session-id"] == [new_session],
        "new namespace request did not use the published session",
    )
    require(
        headers[3]["authorization"]
        == [f"Bearer {kubernetes_token}"]
        and headers[4]["authorization"]
        == [f"Bearer {kubernetes_token}"],
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

    validate_malformed_master_host(
        old_session=old_session,
        new_session=new_session,
        kubernetes_token=kubernetes_token,
        basic_value=basic_value,
        namespace=namespace,
        cluster_name=cluster_name,
        topology_version=topology_version,
    )

    origin = server.root_url
    constructor_cases = [
        (
            "origin path",
            (origin + "/api", old_session, kubernetes_token, "http", 1.0),
        ),
        (
            "empty origin query",
            (origin + "?", old_session, kubernetes_token, "http", 1.0),
        ),
        (
            "empty origin fragment",
            (origin + "#", old_session, kubernetes_token, "http", 1.0),
        ),
        (
            "empty origin port",
            ("http://127.0.0.1:", old_session, kubernetes_token, "http", 1.0),
        ),
        (
            "session newline",
            (origin, "bad\nsession", kubernetes_token, "http", 1.0),
        ),
        (
            "bad Kubernetes scheme",
            (origin, old_session, kubernetes_token, "ftp", 1.0),
        ),
        (
            "boolean timeout",
            (origin, old_session, kubernetes_token, "http", True),
        ),
        (
            "infinite timeout",
            (origin, old_session, kubernetes_token, "http", math.inf),
        ),
    ]
    for label, arguments in constructor_cases:
        expect_raises(
            (TypeError, ValueError),
            lambda arguments=arguments: RotatingVksClient(
                arguments[0],
                arguments[1],
                arguments[2],
                kubernetes_scheme=arguments[3],
                timeout=arguments[4],
            ),
            label,
        )

    host = urlsplit(server.root_url).netloc
    require(host.startswith("127.0.0.1:"), "verifier did not use loopback")
    print("verification passed")


if __name__ == "__main__":
    main()
