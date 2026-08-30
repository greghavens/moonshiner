#!/usr/bin/env python3
"""Protected acceptance verifier for vcf91-0174."""

from __future__ import annotations

import ast
import errno
import http.client
import json
import secrets
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.request import Request

from mock_vcf import ContractPinnedVcfMock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vcf_log_forwarders import (  # noqa: E402 - insert the exercise root first
    ApiError,
    LogManagementClient,
    TokenProviderError,
)


CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/vcf-operations/log-management-openapi.json"
SPEC_BLOB_SHA = "4ada16fa39ec345674de4126174de94ea70d23a0"
OPERATION_IDS = ["getAllLogForwarders", "createLogForwarder"]
SCHEMA_PROPERTIES = [
    "certificate",
    "connectionRefreshInterval",
    "constraints",
    "enabled",
    "forwardComplementaryFields",
    "host",
    "id",
    "name",
    "port",
    "protocol",
    "sslEnabled",
    "tags",
    "transportProtocol",
    "workerCount",
]


def fail(message: str) -> None:
    raise AssertionError(message)


def collect_operation_ids(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "operationId":
                found.append(child)
            else:
                found.extend(collect_operation_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(collect_operation_ids(child))
    return found


def validate_protected_contract() -> dict[str, Any]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))

    if contract.get("openapi") != "3.0.1":
        fail("contract must retain OpenAPI version 3.0.1")
    if contract.get("info") != {
        "title": "Log Management API\n",
        "version": "9.1.0.0",
    }:
        fail("contract must retain the official Log Management API identity")
    if contract.get("x-source") != {
        "kind": "pinned-openapi-specification",
        "repository": "vmware/vcf-api-specs",
        "commitSha": COMMIT,
        "specPath": SPEC_PATH,
        "specBlobSha": SPEC_BLOB_SHA,
        "license": "Apache-2.0",
        "operationIds": OPERATION_IDS,
    }:
        fail("contract source provenance differs from the pinned specification")
    if set(contract.get("paths", {})) != {"/api/v2/logs/forwarders"}:
        fail("contract must contain only the selected log-forwarder path")
    path_item = contract["paths"]["/api/v2/logs/forwarders"]
    if list(path_item) != ["get", "post"]:
        fail("contract must contain only GET then POST on the selected path")
    actual_operations = [
        path_item["get"].get("operationId"),
        path_item["post"].get("operationId"),
    ]
    if actual_operations != OPERATION_IDS:
        fail(f"contract operationIds differ: {actual_operations!r}")
    if collect_operation_ids(contract) != OPERATION_IDS:
        fail("contract contains an unexpected operationId")
    if "200" not in path_item["get"]["responses"]:
        fail("GET success response is missing from the contract")
    if "201" not in path_item["post"]["responses"]:
        fail("POST success response is missing from the contract")

    security = contract["components"]["securitySchemes"][
        "OPSTokenAuthorization"
    ]
    if (
        security.get("in"),
        security.get("name"),
        security.get("type"),
    ) != ("header", "X-JWT-Token", "apiKey"):
        fail("contract authentication is not the specification's X-JWT-Token")
    if "POST /suite-api/api/auth/token/exchange" not in security["description"]:
        fail("contract lost the specification's token-exchange description")

    schema = contract["components"]["schemas"]["LogForwarder"]
    if schema.get("type") != "object":
        fail("LogForwarder must remain an object schema")
    if list(schema.get("properties", {})) != SCHEMA_PROPERTIES:
        fail("LogForwarder property order differs from the pinned specification")
    if schema["properties"]["id"].get("readOnly") is not True:
        fail("LogForwarder.id must remain read-only")
    if schema["properties"]["protocol"].get("enum") != [
        "SYSLOG",
        "RAW",
        "RAWPLUS",
    ]:
        fail("LogForwarder.protocol enum differs from the specification")
    if schema["properties"]["transportProtocol"].get("enum") != ["TCP", "UDP"]:
        fail("LogForwarder.transportProtocol enum differs from the specification")

    if sources.get("repository") != "vmware/vcf-api-specs":
        fail("official source repository is not vmware/vcf-api-specs")
    if sources.get("repositoryUrl") != "https://github.com/vmware/vcf-api-specs":
        fail("official source repository URL differs")
    if sources.get("repositoryCommitSha") != COMMIT:
        fail("official source repository commit is not pinned")
    if sources.get("specPath") != SPEC_PATH:
        fail("official source specification path differs")
    if sources.get("specBlobSha") != SPEC_BLOB_SHA:
        fail("official source specification blob is not pinned")
    if sources.get("license") != "Apache-2.0":
        fail("official source license must be Apache-2.0")
    if sources.get("operationIds") != OPERATION_IDS:
        fail("official source top-level operationIds differ")
    records = sources.get("operations")
    if not isinstance(records, list):
        fail("official source operation records are missing")
    if [record.get("operationId") for record in records] != OPERATION_IDS:
        fail("official_sources.json must record each exact operationId")
    expected_method_paths = [
        ("GET", "/api/v2/logs/forwarders"),
        ("POST", "/api/v2/logs/forwarders"),
    ]
    for record, expected in zip(records, expected_method_paths):
        if (record.get("method"), record.get("path")) != expected:
            fail("official source method/path does not match the specification")
        if record.get("specPath") != SPEC_PATH:
            fail("each operation must record the exact specification path")
        if record.get("repositoryCommitSha") != COMMIT:
            fail("each operation must record the exact repository commit")
        expected_prefix = (
            "https://github.com/vmware/vcf-api-specs/blob/"
            f"{COMMIT}/{SPEC_PATH}"
        )
        if not record.get("sourceUrl", "").startswith(expected_prefix):
            fail("operation source URL is not pinned to the file and commit")
    return contract


def validate_stdlib_package() -> None:
    stdlib = set(sys.stdlib_module_names)
    package = ROOT / "vcf_log_forwarders"
    sources = sorted(package.glob("*.py"))
    if [path.name for path in sources] != ["__init__.py", "client.py"]:
        fail("production package must contain only __init__.py and client.py")
    for source_path in sources:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level = alias.name.split(".", 1)[0]
                    if top_level not in stdlib:
                        fail(f"non-stdlib import in {source_path.name}: {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                top_level = (node.module or "").split(".", 1)[0]
                if top_level not in stdlib:
                    fail(
                        f"non-stdlib import in {source_path.name}: {node.module}"
                    )

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if "dependencies = []" not in pyproject:
        fail("production package must declare no runtime dependencies")
    forbidden_suffixes = {".whl", ".egg", ".zip", ".tar", ".gz", ".so", ".dll"}
    for path in ROOT.rglob("*"):
        if path.is_file() and path.suffix.lower() in forbidden_suffixes:
            fail(f"dependency or binary must not be vendored: {path.relative_to(ROOT)}")


@contextmanager
def running_mock(**kwargs: Any):
    mock = ContractPinnedVcfMock(CONTRACT_PATH, **kwargs)
    loopback_started = False
    try:
        mock.__enter__()
        loopback_started = True
    except OSError as error:
        if error.errno not in {errno.EPERM, errno.EACCES}:
            raise
        mock.enable_in_process_fallback()
    try:
        yield mock
    finally:
        if loopback_started:
            mock.__exit__(None, None, None)


def assert_mock_rejects_uncontracted(mock: ContractPinnedVcfMock) -> None:
    if mock.in_process:
        status, _body = mock._dispatch_in_process(
            Request(
                f"{mock.base_url}/api/v2/logs/forwarders/test",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
        )
        if status != 404:
            fail("loopback mock served an operation absent from the contract")
    else:
        authority = mock.base_url.removeprefix("http://")
        host, raw_port = authority.rsplit(":", 1)
        connection = http.client.HTTPConnection(host, int(raw_port), timeout=5)
        try:
            connection.request(
                "POST",
                "/api/v2/logs/forwarders/test",
                body=b"{}",
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            response.read()
            if response.status != 404:
                fail("loopback mock served an operation absent from the contract")
        finally:
            connection.close()
    if mock.request_log[-1]["operationId"] is not None:
        fail("uncontracted request was mapped to an operationId")
    mock.clear_request_log()


def one_header(record: dict[str, Any], name: str) -> str:
    values = record["headers"].get(name.lower())
    if not isinstance(values, list) or len(values) != 1:
        fail(f"{name} must occur exactly once")
    return values[0]


def compact_json(value: MappingLike) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


MappingLike = dict[str, Any]


def assert_wire_shape(
    request_log: list[dict[str, Any]],
    *,
    old_token: str,
    new_token: str,
    expected_body_a: bytes,
    expected_body_b: bytes,
) -> None:
    expected = [
        ("getAllLogForwarders", "GET", old_token, b"", 200),
        ("createLogForwarder", "POST", old_token, expected_body_a, 201),
        ("createLogForwarder", "POST", old_token, expected_body_b, 403),
        ("createLogForwarder", "POST", new_token, expected_body_b, 201),
    ]
    if len(request_log) != len(expected):
        fail(
            "unexpected request count; reconciliation restarted or completed "
            f"work was replayed ({len(request_log)} requests)"
        )

    for index, (record, wanted) in enumerate(zip(request_log, expected), start=1):
        operation_id, method, token, body, status = wanted
        if record["operationId"] != operation_id:
            fail(f"request {index} used the wrong operationId")
        if record["method"] != method:
            fail(f"request {index} used the wrong HTTP method")
        if record["target"] != "/api/v2/logs/forwarders":
            fail(f"request {index} has an unexpected path or query")
        if record["query"]:
            fail(f"request {index} emitted an unexpected query parameter")
        if one_header(record, "X-JWT-Token") != token:
            fail(f"request {index} has an incorrect X-JWT-Token")
        if one_header(record, "Accept") != "application/json":
            fail(f"request {index} must accept application/json")
        if "authorization" in record["headers"]:
            fail("Authorization must not replace X-JWT-Token")
        if record["body"] != body:
            fail(
                f"request {index} wire bytes differ\n"
                f"expected: {body!r}\nactual:   {record['body']!r}"
            )
        if record["status"] != status:
            fail(f"mock scenario status differs at request {index}")

        if method == "GET":
            if record["body"]:
                fail("GET must be bodyless")
            if "content-type" in record["headers"]:
                fail("GET must omit Content-Type when it has no body")
            if "content-length" in record["headers"]:
                fail("GET must not send an empty entity body")
        else:
            if one_header(record, "Content-Type") != "application/json":
                fail("POST must use exactly application/json")
            if one_header(record, "Content-Length") != str(len(body)):
                fail("POST Content-Length differs from the compact UTF-8 body")

    parsed_a = json.loads(request_log[1]["body"])
    forbidden_a = {
        "certificate",
        "connectionRefreshInterval",
        "constraints",
        "forwardComplementaryFields",
        "id",
        "sslEnabled",
        "tags",
        "workerCount",
        "clientOnly",
    }
    if forbidden_a.intersection(parsed_a):
        fail("unset, empty, unknown, or read-only fields crossed the wire")
    if parsed_a.get("enabled") is not False:
        fail("explicit False was dropped")
    parsed_b = json.loads(request_log[2]["body"])
    if parsed_b.get("connectionRefreshInterval") != 0:
        fail("explicit numeric zero was dropped")
    if request_log[2]["body"] != request_log[3]["body"]:
        fail("the authentication retry changed the failed request body")


def runtime_case() -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    str,
    str,
    bytes,
    bytes,
    str,
]:
    nonce = secrets.token_hex(6)
    existing_name = f"existing-{nonce}"
    name_a = f"edge-a-{nonce}"
    name_b = f"edge-b-{nonce}"
    host_a = f"192.0.2.{1 + int(nonce[:2], 16) % 200}"
    host_b = f"198.51.100.{1 + int(nonce[2:4], 16) % 200}"
    old_token = f"old-{secrets.token_urlsafe(18)}"
    new_token = f"new-{secrets.token_urlsafe(18)}"

    initial = [
        {
            "id": f"initial-{secrets.token_hex(5)}",
            "name": existing_name,
            "host": "203.0.113.10",
            "port": 514,
            "protocol": "SYSLOG",
            "transportProtocol": "UDP",
            "enabled": True,
        }
    ]
    desired = [
        {
            "transportProtocol": "UDP",
            "enabled": True,
            "name": existing_name,
            "protocol": "SYSLOG",
            "port": 514,
            "host": "203.0.113.10",
        },
        {
            "clientOnly": f"omit-{nonce}",
            "transportProtocol": "UDP",
            "protocol": "SYSLOG",
            "port": 514,
            "name": name_a,
            "host": host_a,
            "enabled": False,
            "certificate": "",
            "sslEnabled": None,
            "tags": {},
            "workerCount": [],
            "id": f"read-only-{nonce}",
        },
        {
            "forwardComplementaryFields": "",
            "constraints": None,
            "transportProtocol": "TCP",
            "tags": {"site": f"dc-{nonce}"},
            "sslEnabled": True,
            "protocol": "RAW",
            "port": 6514,
            "name": name_b,
            "host": host_b,
            "enabled": True,
            "connectionRefreshInterval": 0,
        },
    ]
    body_a = compact_json(
        {
            "enabled": False,
            "host": host_a,
            "name": name_a,
            "port": 514,
            "protocol": "SYSLOG",
            "transportProtocol": "UDP",
        }
    )
    body_b = compact_json(
        {
            "connectionRefreshInterval": 0,
            "enabled": True,
            "host": host_b,
            "name": name_b,
            "port": 6514,
            "protocol": "RAW",
            "sslEnabled": True,
            "tags": {"site": f"dc-{nonce}"},
            "transportProtocol": "TCP",
        }
    )
    return initial, desired, old_token, new_token, body_a, body_b, nonce


def run_refresh_scenario() -> None:
    (
        initial,
        desired,
        old_token,
        new_token,
        body_a,
        body_b,
        nonce,
    ) = runtime_case()
    created_ids: list[str] = []

    def created_id_factory(index: int) -> str:
        value = f"created-{index}-{secrets.token_hex(5)}"
        created_ids.append(value)
        return value

    with running_mock(
        old_token=old_token,
        new_token=new_token,
        initial_forwarders=initial,
        created_id_factory=created_id_factory,
        expire_after_successful_posts=1,
    ) as mock:
        assert_mock_rejects_uncontracted(mock)

        invalid_provider_calls: list[bool] = []

        def should_not_run(force_refresh: bool) -> str:
            invalid_provider_calls.append(force_refresh)
            return old_token

        invalid_client = LogManagementClient(mock.base_url, should_not_run)
        mock.install_fallback_transport(invalid_client)
        try:
            invalid_client.reconcile_forwarders(
                [
                    {"name": f"valid-{nonce}"},
                    {"name": f" padded-{nonce} "},
                ]
            )
        except ValueError:
            pass
        else:
            fail("invalid desired input was accepted")
        if invalid_provider_calls or mock.request_log:
            fail("desired validation must finish before token acquisition or traffic")

        provider_calls: list[bool] = []

        def token_provider(force_refresh: bool) -> str:
            provider_calls.append(force_refresh)
            return new_token if force_refresh else old_token

        client = LogManagementClient(
            mock.base_url, token_provider, timeout=3.0
        )
        mock.install_fallback_transport(client)
        reconciled = client.reconcile_forwarders(desired)

        if provider_calls != [False, True]:
            fail(
                "provider calls must be exactly one initial acquisition and "
                f"one forced refresh, got {provider_calls!r}"
            )
        expected_names = [
            initial[0]["name"],
            desired[1]["name"],
            desired[2]["name"],
        ]
        if [item.get("name") for item in reconciled] != expected_names:
            fail("reconciled output lost, reordered, or duplicated work")
        if len(reconciled) != 3 or len(created_ids) != 2:
            fail("reconciled output did not preserve one existing and two creates")
        if [item.get("name") for item in mock.forwarders] != expected_names:
            fail("mock server state lost or duplicated a forwarder")
        if len(mock.forwarders) != 3:
            fail("mock server state has an unexpected item count")

        assert_wire_shape(
            mock.request_log,
            old_token=old_token,
            new_token=new_token,
            expected_body_a=body_a,
            expected_body_b=body_b,
        )


def run_second_auth_failure_scenario() -> None:
    nonce = secrets.token_hex(5)
    old_token = f"old-{secrets.token_urlsafe(15)}"
    accepted_new_token = f"accepted-{secrets.token_urlsafe(15)}"
    rejected_refresh = f"rejected-{secrets.token_urlsafe(15)}"
    provider_calls: list[bool] = []

    def token_provider(force_refresh: bool) -> str:
        provider_calls.append(force_refresh)
        return rejected_refresh if force_refresh else old_token

    with running_mock(
        old_token=old_token,
        new_token=accepted_new_token,
        initial_forwarders=[],
        created_id_factory=lambda index: f"unused-{index}-{nonce}",
        expire_after_successful_posts=0,
    ) as mock:
        client = LogManagementClient(mock.base_url, token_provider, timeout=3)
        mock.install_fallback_transport(client)
        try:
            client.reconcile_forwarders([{"name": f"terminal-{nonce}"}])
        except ApiError as error:
            if error.status_code != 403 or error.error_code != "SECURITY_ERROR":
                fail("second authentication failure lost ErrorBody details")
            if old_token in str(error) or rejected_refresh in str(error):
                fail("ApiError exposed an access token")
        else:
            fail("a second authentication failure was not terminal")
        if provider_calls != [False, True]:
            fail("second auth failure caused an extra provider call")
        if len(mock.request_log) != 3:
            fail("second auth failure caused an extra retry or reconciliation restart")
        if [record["method"] for record in mock.request_log] != [
            "GET",
            "POST",
            "POST",
        ]:
            fail("second auth failure did not retry only the interrupted create")
        if mock.forwarders:
            fail("failed authentication unexpectedly mutated server state")


def run_invalid_provider_scenario() -> None:
    token = f"accepted-{secrets.token_urlsafe(15)}"
    calls: list[bool] = []

    def invalid_provider(force_refresh: bool) -> str:
        calls.append(force_refresh)
        return "unsafe\nheader"

    with running_mock(
        old_token=token,
        new_token=f"new-{secrets.token_urlsafe(15)}",
        initial_forwarders=[],
        created_id_factory=lambda index: f"unused-{index}",
        expire_after_successful_posts=0,
    ) as mock:
        client = LogManagementClient(mock.base_url, invalid_provider)
        mock.install_fallback_transport(client)
        try:
            client.list_forwarders()
        except TokenProviderError:
            pass
        else:
            fail("CR/LF-containing provider token was accepted")
        if calls != [False]:
            fail("invalid initial token caused an unexpected provider call")
        if mock.request_log:
            fail("invalid provider token reached the wire")


def run_constructor_validation() -> None:
    calls: list[bool] = []

    def provider(force_refresh: bool) -> str:
        calls.append(force_refresh)
        return "unused-token"

    invalid_origins = [
        "ftp://127.0.0.1",
        "http://user@127.0.0.1",
        "http://127.0.0.1/api",
        "http://127.0.0.1?",
        "http://127.0.0.1#",
    ]
    for value in invalid_origins:
        try:
            LogManagementClient(value, provider)
        except ValueError:
            pass
        else:
            fail(f"non-origin base_url was accepted: {value!r}")
    for value in (0, -1, False, float("nan"), float("inf")):
        try:
            LogManagementClient("http://127.0.0.1:1", provider, timeout=value)
        except ValueError:
            pass
        else:
            fail(f"nonpositive or non-finite timeout was accepted: {value!r}")
    if calls:
        fail("constructor validation called the access-token provider")


def main() -> int:
    try:
        validate_protected_contract()
        validate_stdlib_package()
        run_refresh_scenario()
        run_second_auth_failure_scenario()
        run_invalid_provider_scenario()
        run_constructor_validation()
    except NotImplementedError:
        print("verification failed: client implementation is incomplete", file=sys.stderr)
        return 1
    except (
        AssertionError,
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
        SyntaxError,
        TypeError,
        ValueError,
    ) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1
    print("verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
