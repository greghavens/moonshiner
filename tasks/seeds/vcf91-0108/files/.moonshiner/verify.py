#!/usr/bin/env python3
"""Protected verification for expiry-safe, deterministically sorted inventory."""

from __future__ import annotations

import ast
import base64
import importlib
import json
import math
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / "tools" / "mock_vcenter.py"
PACKAGE_ROOT = ROOT / "vcf_inventory"
COMMIT_SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_BLOB_SHA = "8028b0824c4ff3503d05f44814f967938a795c40"
SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
OPERATION_IDS = [
    "Cis.Session_create",
    "Vcenter.Datacenter_list",
    "Vcenter.VM_list",
]
DATACENTER_FILTERS = ["datacenters", "names", "folders"]
VM_FILTERS = [
    "vms",
    "names",
    "folders",
    "datacenters",
    "hosts",
    "clusters",
    "resource_pools",
    "power_states",
]
POWER_STATES = {"POWERED_OFF", "POWERED_ON", "SUSPENDED"}
PUBLIC_EXPORTS = [
    "ProtocolError",
    "VcenterError",
    "VcenterInventoryClient",
]


class VerificationFailure(AssertionError):
    """A protected acceptance assertion failed."""


def require(condition: object, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def load_object(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    require(isinstance(value, dict), f"{path} must contain a JSON object")
    return value


def verify_provenance(contract: dict, sources: dict) -> None:
    require(
        contract.get("source")
        == {
            "kind": "pinned-openapi-specification",
            "repository": "vmware/vcf-api-specs",
            "commitSha": COMMIT_SHA,
            "specPath": SPEC_PATH,
            "specBlobSha": SPEC_BLOB_SHA,
            "license": "Apache-2.0",
            "openapi": "3.0.3",
            "apiVersion": "9.1.0.0",
            "serverTemplate": "https://{host}/api",
            "basePath": "/api",
        },
        "contract source is not pinned to the VCF 9.1 YAML",
    )
    require(
        contract.get("securitySchemes")
        == {
            "basic_auth": {"type": "http", "scheme": "basic"},
            "api_key_auth": {
                "type": "apiKey",
                "in": "header",
                "name": "vmware-api-session-id",
            },
        },
        "contract security projection changed",
    )
    operations = contract.get("operations")
    require(
        isinstance(operations, list)
        and [item.get("operationId") for item in operations] == OPERATION_IDS,
        "focused operationIds or order changed",
    )
    expected = [
        ("POST", "/session", "/api/session", ["basic_auth"], []),
        (
            "GET",
            "/vcenter/datacenter",
            "/api/vcenter/datacenter",
            ["api_key_auth"],
            DATACENTER_FILTERS,
        ),
        (
            "GET",
            "/vcenter/vm",
            "/api/vcenter/vm",
            ["api_key_auth"],
            VM_FILTERS,
        ),
    ]
    for operation, projection in zip(operations, expected):
        method, spec_path, path, security, parameter_names = projection
        require(
            operation.get("method") == method
            and operation.get("specPathItem") == spec_path
            and operation.get("path") == path
            and operation.get("requestBody") is None
            and operation.get("security") == security,
            f"wire projection changed for {operation.get('operationId')}",
        )
        if parameter_names:
            parameters = operation.get("parameters")
            require(
                isinstance(parameters, list)
                and [item.get("name") for item in parameters]
                == parameter_names,
                f"filter order changed for {operation.get('operationId')}",
            )
            for parameter in parameters:
                require(
                    parameter.get("in") == "query"
                    and parameter.get("required") is False
                    and parameter.get("style") == "form"
                    and parameter.get("explode") is True
                    and parameter.get("type") == "array"
                    and parameter.get("uniqueItems") is True,
                    f"filter wire shape changed for {parameter.get('name')}",
                )

    require(
        operations[0].get("responses", {}).get("201")
        == {
            "contentType": "application/json",
            "schema": {"type": "string", "format": "password"},
        },
        "session-create success projection changed",
    )
    require(
        operations[1].get("responses", {}).get("200")
        == {
            "contentType": "application/json",
            "schema": {
                "type": "array",
                "items": "Vcenter.Datacenter.Summary",
            },
        },
        "datacenter-list success projection changed",
    )
    require(
        operations[2].get("responses", {}).get("200")
        == {
            "contentType": "application/json",
            "schema": {
                "type": "array",
                "items": "Vcenter.VM.Summary",
            },
        },
        "VM-list success projection changed",
    )
    schemas = contract.get("schemas")
    require(isinstance(schemas, dict), "contract schemas are missing")
    datacenter_summary = schemas.get("Vcenter.Datacenter.Summary")
    require(
        isinstance(datacenter_summary, dict)
        and datacenter_summary.get("type") == "object"
        and datacenter_summary.get("required") == ["datacenter", "name"]
        and list(datacenter_summary.get("properties", {}))
        == ["datacenter", "name"],
        "datacenter summary projection changed",
    )
    vm_summary = schemas.get("Vcenter.VM.Summary")
    require(
        isinstance(vm_summary, dict)
        and vm_summary.get("type") == "object"
        and vm_summary.get("required") == ["name", "power_state", "vm"]
        and list(vm_summary.get("properties", {}))
        == [
            "vm",
            "name",
            "power_state",
            "cpu_count",
            "memory_size_mib",
        ]
        and vm_summary["properties"]["power_state"].get("enum")
        == ["POWERED_OFF", "POWERED_ON", "SUSPENDED"],
        "VM summary projection changed",
    )

    require(
        sources.get("repository") == "vmware/vcf-api-specs"
        and sources.get("repositoryCommitSha") == COMMIT_SHA
        and sources.get("specPath") == SPEC_PATH
        and sources.get("specBlobSha") == SPEC_BLOB_SHA
        and sources.get("license") == "Apache-2.0"
        and sources.get("operationIds") == OPERATION_IDS,
        "official source summary is not pinned",
    )
    source_operations = sources.get("operations")
    require(
        isinstance(source_operations, list)
        and [item.get("operationId") for item in source_operations]
        == OPERATION_IDS,
        "official source operation records are incomplete",
    )
    for item in source_operations:
        require(
            item.get("repositoryCommitSha") == COMMIT_SHA
            and item.get("specPath") == SPEC_PATH,
            f"{item.get('operationId')} lacks an independent source pin",
        )


def verify_stdlib_only() -> None:
    require(PACKAGE_ROOT.is_dir(), "vcf_inventory package is missing")
    python_files = sorted(PACKAGE_ROOT.rglob("*.py"))
    require(python_files, "vcf_inventory contains no Python files")
    stdlib = getattr(sys, "stdlib_module_names", frozenset())
    for path in python_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as error:
            raise VerificationFailure(f"{path} has invalid Python: {error}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_name = alias.name.split(".", 1)[0]
                    require(
                        root_name in stdlib or root_name == "vcf_inventory",
                        f"{path} imports non-stdlib module {root_name}",
                    )
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                root_name = (node.module or "").split(".", 1)[0]
                require(
                    root_name in stdlib or root_name == "vcf_inventory",
                    f"{path} imports non-stdlib module {root_name}",
                )


def wait_for_port(process: subprocess.Popen[str], port_file: Path) -> int:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _stdout, stderr = process.communicate()
            raise VerificationFailure(
                f"loopback mock exited before startup: {stderr.strip()}"
            )
        if port_file.exists():
            text = port_file.read_text(encoding="utf-8").strip()
            if text:
                port = int(text)
                require(0 < port < 65536, "mock reported an invalid port")
                return port
        time.sleep(0.01)
    raise VerificationFailure("timed out waiting for loopback mock")


def start_mock(
    temp: Path,
    scenario: dict,
) -> tuple[subprocess.Popen[str], int, Path]:
    temp.mkdir(parents=True, exist_ok=True)
    port_file = temp / "port"
    log_file = temp / "requests.jsonl"
    scenario_file = temp / "scenario.json"
    scenario_file.write_text(
        json.dumps(scenario, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(MOCK_PATH),
            str(port_file),
            str(log_file),
            str(CONTRACT_PATH),
            str(scenario_file),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return process, wait_for_port(process, port_file), log_file


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def read_log(log_file: Path) -> list[dict]:
    if not log_file.exists():
        return []
    return [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line
    ]


def read_log_exact(log_file: Path, expected: int) -> list[dict]:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        entries = read_log(log_file)
        if len(entries) >= expected:
            time.sleep(0.05)
            entries = read_log(log_file)
            require(
                len(entries) == expected,
                f"expected {expected} requests, observed {len(entries)}",
            )
            return entries
        time.sleep(0.01)
    raise VerificationFailure(
        f"request log did not reach {expected} entries"
    )


def expect_local_error(
    function: object,
    args: tuple = (),
    kwargs: dict | None = None,
) -> None:
    try:
        function(*args, **(kwargs or {}))
    except (TypeError, ValueError):
        return
    except Exception as error:
        raise VerificationFailure(
            f"invalid input escaped local validation as {type(error).__name__}"
        ) from error
    raise VerificationFailure("invalid input was accepted")


def verify_constructor_validation(package: object) -> None:
    client_type = package.VcenterInventoryClient
    for base_url in (
        None,
        "",
        "127.0.0.1:1",
        "ftp://127.0.0.1:1",
        "http://user:pass@127.0.0.1:1",
        "http://127.0.0.1:1/sdk",
        "http://127.0.0.1:1?x=1",
        "http://127.0.0.1:1#fragment",
        "http://127.0.0.1:1/\rignored",
    ):
        expect_local_error(
            client_type,
            (base_url, "svc", "password"),
        )
    for username in (None, "", "  ", "bad:name", "bad\rname", "bad\nname"):
        expect_local_error(
            client_type,
            ("http://127.0.0.1:1", username, "password"),
        )
    for password in (None, "", "  ", "bad\rsecret", "bad\nsecret"):
        expect_local_error(
            client_type,
            ("http://127.0.0.1:1", "svc", password),
        )
    for timeout in (None, True, False, 0, -1, math.inf, -math.inf, math.nan):
        expect_local_error(
            client_type,
            ("http://127.0.0.1:1", "svc", "password"),
            {"timeout": timeout},
        )


def expected_basic(username: str, password: str) -> str:
    raw = f"{username}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def expected_target(path: str, order: list[str], filters: dict) -> str:
    pairs: list[str] = []
    for name in order:
        selected = filters.get(name)
        if selected is None:
            continue
        for value in selected:
            pairs.append(
                f"{quote(name, safe='-._~')}="
                f"{quote(value, safe='-._~')}"
            )
    return path + ("?" + "&".join(pairs) if pairs else "")


def require_bodyless(entry: dict) -> None:
    require(
        entry.get("bodyLength") == 0
        and entry.get("bodyHex") == ""
        and entry.get("contentType") is None
        and entry.get("transferEncoding") is None,
        f"{entry.get('operationId')} request was not bodyless",
    )


def capture_error(
    expected_type: type[BaseException],
    function: object,
    *args: object,
    **kwargs: object,
) -> BaseException:
    try:
        function(*args, **kwargs)
    except expected_type as error:
        return error
    except Exception as error:
        raise VerificationFailure(
            f"expected {expected_type.__name__}, got {type(error).__name__}"
        ) from error
    raise VerificationFailure(f"expected {expected_type.__name__}")


def assert_safe_error(
    error: BaseException,
    secrets_to_hide: list[str],
) -> None:
    rendered = str(error) + "\n" + repr(error)
    for secret in secrets_to_hide:
        require(secret not in rendered, "exception rendering exposed a secret")


def verify_filter_validation(client: object, log_file: Path) -> None:
    initial_count = len(read_log(log_file))
    invalid_common = (
        [],
        (),
        "",
        "one-value",
        [""],
        ["   "],
        ["same", "same"],
        [1],
    )
    for value in invalid_common:
        expect_local_error(client.list_datacenters, kwargs={"names": value})
        expect_local_error(client.list_vms, kwargs={"names": value})
    expect_local_error(
        client.list_vms,
        kwargs={"power_states": ["NOT_A_POWER_STATE"]},
    )
    require(
        len(read_log(log_file)) == initial_count,
        "invalid collection filters opened a connection",
    )


def verify_primary(package: object, temp: Path) -> tuple[int, list[str]]:
    suffix = secrets.token_hex(8)
    username = f"inventory-svc-{suffix}"
    password = f"päss:{secrets.token_urlsafe(16)}"
    tokens = [
        f"session-one-{secrets.token_urlsafe(18)}",
        f"session-two-{secrets.token_urlsafe(18)}",
        f"session-three-{secrets.token_urlsafe(18)}",
    ]
    error_secret = f"server-detail-{secrets.token_urlsafe(18)}"
    protocol_name = f"protocol-{suffix}"
    failure_name = f"failure-{suffix}"
    perpetual_401_name = f"expired-again-{suffix}"

    datacenters = [
        {"datacenter": f"dc-middle-{suffix}", "name": f"Middle {suffix}"},
        {"datacenter": f"dc-zulu-{suffix}", "name": f"Zulu {suffix}"},
        {"datacenter": f"dc-alpha-{suffix}", "name": f"Alpha {suffix}"},
    ]
    vms = [
        {
            "vm": f"vm-middle-{suffix}",
            "name": f"Middle VM {suffix}",
            "power_state": "SUSPENDED",
            "cpu_count": 2,
        },
        {
            "vm": f"vm-zulu-{suffix}",
            "name": f"Zulu VM {suffix}",
            "power_state": "POWERED_OFF",
            "memory_size_mib": 4096,
        },
        {
            "vm": f"vm-alpha-{suffix}",
            "name": f"Alpha VM {suffix}",
            "power_state": "POWERED_ON",
            "cpu_count": None,
            "memory_size_mib": None,
        },
    ]
    scenario = {
        "username": username,
        "password": password,
        "tokens": tokens,
        "datacenters": datacenters,
        "vms": vms,
        "error_secret": error_secret,
        "protocol_name": protocol_name,
        "failure_name": failure_name,
        "perpetual_401_name": perpetual_401_name,
    }
    process, port, log_file = start_mock(temp, scenario)
    old_proxy = {
        name: os.environ.get(name)
        for name in ("HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy")
    }
    os.environ["HTTP_PROXY"] = "http://127.0.0.1:1"
    os.environ["http_proxy"] = "http://127.0.0.1:1"
    os.environ["NO_PROXY"] = ""
    os.environ["no_proxy"] = ""
    try:
        client = package.VcenterInventoryClient(
            f"http://127.0.0.1:{port}/",
            username,
            password,
            timeout=3.0,
        )
        snapshot = client.collect_inventory()
        expected_datacenters = sorted(
            [dict(item) for item in datacenters],
            key=lambda item: (item["name"], item["datacenter"]),
        )
        expected_vms = sorted(
            [dict(item) for item in vms],
            key=lambda item: (item["name"], item["vm"]),
        )
        require(
            snapshot
            == {
                "datacenters": expected_datacenters,
                "vms": expected_vms,
            },
            "collect_inventory did not preserve and sort both collections",
        )
        first_entries = read_log_exact(log_file, 5)
        require(
            [
                (item["operationId"], item["responseStatus"])
                for item in first_entries
            ]
            == [
                ("Cis.Session_create", 201),
                ("Vcenter.Datacenter_list", 200),
                ("Vcenter.VM_list", 401),
                ("Cis.Session_create", 201),
                ("Vcenter.VM_list", 200),
            ],
            "expiry recovery restarted work or replayed the wrong operation",
        )
        require(
            first_entries[1]["vmwareApiSessionId"] == tokens[0]
            and first_entries[2]["vmwareApiSessionId"] == tokens[0]
            and first_entries[4]["vmwareApiSessionId"] == tokens[1],
            "session replacement was not confined to the failed operation",
        )

        dc_filters = {
            "datacenters": [
                f"dc/α ?{suffix}",
                f"dc&={suffix}",
            ],
            "names": [
                f"Blue / 雪 ?&={suffix}",
                f"Name+Two={suffix}",
            ],
            "folders": [f"group / + ={suffix}"],
        }
        vm_filters = {
            "vms": [f"vm/β ?{suffix}"],
            "names": [f"Guest / 雪 ?&={suffix}"],
            "folders": [f"folder&={suffix}"],
            "datacenters": [f"dc+={suffix}"],
            "hosts": [f"host/{suffix}"],
            "clusters": [f"cluster ?{suffix}"],
            "resource_pools": [f"pool &{suffix}"],
            "power_states": ["POWERED_ON", "SUSPENDED"],
        }
        dc_first = client.list_datacenters(**dc_filters)
        dc_second = client.list_datacenters()
        vm_first = client.list_vms(**vm_filters)
        vm_second = client.list_vms()
        require(
            dc_first == expected_datacenters
            and dc_second == expected_datacenters
            and vm_first == expected_vms
            and vm_second == expected_vms,
            "collection output depends on server element order",
        )
        require(
            all(
                first is not second
                for first, second in zip(dc_first, dc_second)
            )
            and all(
                first is not second
                for first, second in zip(vm_first, vm_second)
            ),
            "collection methods reused item dictionaries across calls",
        )
        entries = read_log_exact(log_file, 9)
        require(
            entries[5]["rawTarget"]
            == expected_target(
                "/api/vcenter/datacenter",
                DATACENTER_FILTERS,
                dc_filters,
            )
            and entries[6]["rawTarget"] == "/api/vcenter/datacenter"
            and entries[7]["rawTarget"]
            == expected_target(
                "/api/vcenter/vm",
                VM_FILTERS,
                vm_filters,
            )
            and entries[8]["rawTarget"] == "/api/vcenter/vm",
            "collection filters were not exploded, ordered, encoded, or omitted",
        )
        require(
            entries[1]["responseIds"]
            == [item["datacenter"] for item in datacenters]
            and entries[5]["responseIds"]
            == [item["datacenter"] for item in reversed(datacenters)]
            and entries[6]["responseIds"]
            == [item["datacenter"] for item in datacenters],
            "mock did not alternate datacenter response order",
        )
        require(
            entries[4]["responseIds"] == [item["vm"] for item in vms]
            and entries[7]["responseIds"]
            == [item["vm"] for item in reversed(vms)]
            and entries[8]["responseIds"] == [item["vm"] for item in vms],
            "mock did not alternate VM response order",
        )

        verify_filter_validation(client, log_file)

        protocol_error = capture_error(
            package.ProtocolError,
            client.list_datacenters,
            names=[protocol_name],
        )
        require(
            getattr(protocol_error, "operation_id", None)
            == "Vcenter.Datacenter_list",
            "ProtocolError lost its operationId",
        )
        assert_safe_error(
            protocol_error,
            [password, *tokens, error_secret],
        )

        server_error = capture_error(
            package.VcenterError,
            client.list_vms,
            names=[failure_name],
        )
        require(
            getattr(server_error, "operation_id", None) == "Vcenter.VM_list"
            and getattr(server_error, "status_code", None) == 503
            and isinstance(getattr(server_error, "payload", None), dict)
            and error_secret
            in json.dumps(server_error.payload, ensure_ascii=False),
            "VcenterError did not preserve safe structured failure fields",
        )
        assert_safe_error(server_error, [password, *tokens, error_secret])

        expired_error = capture_error(
            package.VcenterError,
            client.list_vms,
            names=[perpetual_401_name],
        )
        require(
            getattr(expired_error, "operation_id", None) == "Vcenter.VM_list"
            and getattr(expired_error, "status_code", None) == 401,
            "a second 401 did not escape as the focused operation failure",
        )
        assert_safe_error(expired_error, [password, *tokens, error_secret])

        entries = read_log_exact(log_file, 14)
        tail = [
            (item["operationId"], item["responseStatus"])
            for item in entries[9:]
        ]
        require(
            tail
            == [
                ("Vcenter.Datacenter_list", 200),
                ("Vcenter.VM_list", 503),
                ("Vcenter.VM_list", 401),
                ("Cis.Session_create", 201),
                ("Vcenter.VM_list", 401),
            ],
            "non-401 retry or unbounded expiry replay was observed",
        )
        require(
            entries[11]["rawTarget"] == entries[13]["rawTarget"]
            and entries[11]["vmwareApiSessionId"] == tokens[1]
            and entries[13]["vmwareApiSessionId"] == tokens[2],
            "the one allowed replay changed the request or kept the old token",
        )
        expected_authorization = expected_basic(username, password)
        for index, entry in enumerate(entries):
            require_bodyless(entry)
            require(
                entry.get("accept") == "application/json",
                "a request omitted the contract media type",
            )
            if entry["operationId"] == "Cis.Session_create":
                require(
                    entry.get("authorization") == expected_authorization
                    and entry.get("vmwareApiSessionId") is None,
                    "session creation mixed or corrupted authentication",
                )
            else:
                require(
                    entry.get("authorization") is None
                    and entry.get("vmwareApiSessionId") in tokens,
                    f"collection request {index} mixed authentication schemes",
                )
        return port, [password, *tokens, error_secret]
    finally:
        for name, value in old_proxy.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        stop_process(process)


def verify_transport_redaction(
    package: object,
    port: int,
    hidden: list[str],
) -> None:
    transport_password = f"transport-{secrets.token_urlsafe(16)}"
    error = capture_error(
        package.VcenterError,
        package.VcenterInventoryClient,
        f"http://127.0.0.1:{port}",
        "transport-user",
        transport_password,
        timeout=0.25,
    )
    require(
        getattr(error, "operation_id", None) == "Cis.Session_create"
        and getattr(error, "status_code", "sentinel") is None
        and getattr(error, "payload", "sentinel") is None,
        "transport failure fields are incorrect",
    )
    assert_safe_error(error, [transport_password, *hidden])


def main() -> int:
    contract = load_object(CONTRACT_PATH)
    sources = load_object(SOURCES_PATH)
    verify_provenance(contract, sources)
    verify_stdlib_only()

    try:
        package = importlib.import_module("vcf_inventory")
    except Exception as error:
        raise VerificationFailure(
            f"could not import vcf_inventory: {type(error).__name__}: {error}"
        ) from error
    require(
        getattr(package, "__all__", None) == PUBLIC_EXPORTS,
        "vcf_inventory.__all__ changed",
    )
    verify_constructor_validation(package)

    with tempfile.TemporaryDirectory(prefix="vcf-inventory-verify-") as raw:
        port, hidden = verify_primary(package, Path(raw))
        verify_transport_redaction(package, port, hidden)

    print("verification passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationFailure as error:
        print(f"verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
