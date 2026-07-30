#!/usr/bin/env python3
"""Protected deterministic verification for drain-safe session rotation."""

from __future__ import annotations

import ast
import base64
import concurrent.futures
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
PACKAGE_ROOT = ROOT / "vcf_session_rotation"
COMMIT_SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_BLOB_SHA = "8028b0824c4ff3503d05f44814f967938a795c40"
SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
OPERATION_IDS = [
    "Cis.Session_create",
    "Vcenter.VM_list",
    "Cis.Session_delete",
]
FILTER_NAMES = [
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
    "RotatingVcenterClient",
    "VcenterError",
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
    source = contract.get("source")
    require(
        source
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
        "contract source metadata is not pinned to the VCF 9.1 YAML",
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
        "contract operationIds or order changed",
    )
    expected = [
        ("POST", "/session", "/api/session", ["basic_auth"]),
        ("GET", "/vcenter/vm", "/api/vcenter/vm", ["api_key_auth"]),
        ("DELETE", "/session", "/api/session", ["api_key_auth"]),
    ]
    for operation, projection in zip(operations, expected):
        method, spec_path, path, security = projection
        require(
            operation.get("method") == method
            and operation.get("specPathItem") == spec_path
            and operation.get("path") == path
            and operation.get("requestBody") is None
            and operation.get("security") == security,
            f"wire projection changed for {operation.get('operationId')}",
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
                "items": "Vcenter.VM.Summary",
            },
        },
        "VM-list success projection changed",
    )
    require(
        operations[2].get("responses", {}).get("204") == {"content": False},
        "session-delete success projection changed",
    )

    parameters = operations[1].get("parameters")
    require(
        isinstance(parameters, list)
        and [item.get("name") for item in parameters] == FILTER_NAMES,
        "VM-list optional-filter order changed",
    )
    for parameter in parameters:
        require(
            parameter.get("in") == "query"
            and parameter.get("required") is False
            and parameter.get("style") == "form"
            and parameter.get("explode") is True
            and parameter.get("type") == "array"
            and parameter.get("uniqueItems") is True,
            f"wire projection changed for {parameter.get('name')}",
        )

    summary = contract.get("schemas", {}).get("Vcenter.VM.Summary")
    require(
        isinstance(summary, dict)
        and summary.get("type") == "object"
        and summary.get("required") == ["name", "power_state", "vm"],
        "VM summary required fields changed",
    )
    properties = summary.get("properties")
    require(
        isinstance(properties, dict)
        and list(properties)
        == [
            "vm",
            "name",
            "power_state",
            "cpu_count",
            "memory_size_mib",
        ]
        and properties["power_state"].get("enum")
        == ["POWERED_OFF", "POWERED_ON", "SUSPENDED"],
        "VM summary schema projection changed",
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
            f"{item.get('operationId')} lacks its independent source pin",
        )


def verify_stdlib_only() -> None:
    require(PACKAGE_ROOT.is_dir(), "vcf_session_rotation package is missing")
    python_files = sorted(PACKAGE_ROOT.rglob("*.py"))
    require(python_files, "vcf_session_rotation contains no Python files")
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
                        root_name in stdlib
                        or root_name == "vcf_session_rotation",
                        f"{path} imports non-stdlib module {root_name}",
                    )
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                root_name = (node.module or "").split(".", 1)[0]
                require(
                    root_name in stdlib
                    or root_name == "vcf_session_rotation",
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


def read_log(log_file: Path) -> list[dict]:
    if not log_file.exists():
        return []
    return [
        json.loads(line)
        for line in log_file.read_text(encoding="utf-8").splitlines()
        if line
    ]


def wait_for_log(log_file: Path, minimum: int) -> list[dict]:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        entries = read_log(log_file)
        if len(entries) >= minimum:
            return entries
        time.sleep(0.01)
    raise VerificationFailure(
        f"request log did not reach {minimum} entries"
    )


def read_log_exact(log_file: Path, expected: int) -> list[dict]:
    entries = wait_for_log(log_file, expected)
    time.sleep(0.08)
    entries = read_log(log_file)
    require(
        len(entries) == expected,
        f"expected {expected} requests, observed {len(entries)}",
    )
    return entries


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


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


def verify_local_constructor_validation(package: object) -> None:
    client_type = package.RotatingVcenterClient
    for base_url in (
        None,
        "",
        "vc.example.test",
        "ftp://vc.example.test",
        "http://user:pass@vc.example.test",
        "http://vc.example.test/sdk",
        "http://vc.example.test?x=1",
        "http://vc.example.test#fragment",
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


def expected_target(filters: dict[str, list[str]]) -> str:
    pairs: list[str] = []
    for name in FILTER_NAMES:
        for value in filters.get(name, []):
            pairs.append(
                f"{quote(name, safe='-._~')}="
                f"{quote(value, safe='-._~')}"
            )
    return "/api/vcenter/vm?" + "&".join(pairs)


def require_bodyless(entry: dict, operation: str) -> None:
    require(
        entry.get("bodyLength") == 0
        and entry.get("bodyHex") == ""
        and entry.get("contentType") is None
        and entry.get("transferEncoding") is None,
        f"{operation} was not bodyless",
    )


def verify_primary(package: object, temp: Path) -> None:
    suffix = secrets.token_hex(8)
    username = f"rotation-svc-{suffix}"
    old_password = f"old:{secrets.token_urlsafe(16)}"
    new_password = f"new:{secrets.token_urlsafe(16)}"
    old_token = f"old-session-{secrets.token_urlsafe(18)}"
    new_token = f"new-session-{secrets.token_urlsafe(18)}"
    error_secret = f"server-detail-{secrets.token_urlsafe(18)}"
    release_file = temp / "release-old-request"
    slow_filters = {
        "vms": [f"vm/α ?{suffix}", f"vm&={suffix}"],
        "names": [f"blue / 雪 ?&={suffix}", f"name+two={suffix}"],
        "datacenters": [f"dc / + ={suffix}"],
        "power_states": ["POWERED_ON", "SUSPENDED"],
    }
    slow_vms = [
        {
            "vm": f"vm-old-{suffix}",
            "name": f"Old request {suffix}",
            "power_state": "POWERED_ON",
            "cpu_count": 4,
        }
    ]
    fast_vms = [
        {
            "vm": f"vm-new-{suffix}",
            "name": f"New request {suffix}",
            "power_state": "SUSPENDED",
            "memory_size_mib": 8192,
        }
    ]
    scenario = {
        "username": username,
        "old_password": old_password,
        "new_password": new_password,
        "old_token": old_token,
        "new_token": new_token,
        "error_secret": error_secret,
        "release_file": str(release_file),
        "slow_filters": slow_filters,
        "slow_vms": slow_vms,
        "fast_vms": fast_vms,
    }
    process, port, log_file = start_mock(temp / "mock", scenario)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    client = None
    try:
        client = package.RotatingVcenterClient(
            f"http://127.0.0.1:{port}",
            username,
            old_password,
            timeout=3.0,
        )
        require(client.__enter__() is client, "__enter__ must return self")
        read_log_exact(log_file, 1)

        old_future = executor.submit(client.list_vms, **slow_filters)
        wait_for_log(log_file, 2)
        require(not old_future.done(), "old-session request was not held open")

        rotate_future = executor.submit(client.rotate_password, new_password)
        entries = wait_for_log(log_file, 3)
        require(
            entries[2].get("operationId") == "Cis.Session_create"
            and entries[2].get("credentialGeneration") == "new",
            "rotation did not create the replacement session",
        )
        time.sleep(0.08)
        require(
            not rotate_future.done(),
            "rotation retired the old session before its request drained",
        )

        fast_result = client.list_vms()
        require(
            fast_result == fast_vms
            and fast_result is not fast_vms
            and all(
                isinstance(item, dict)
                and item is not expected
                for item, expected in zip(fast_result, fast_vms)
            ),
            "queryless replacement-session result was not preserved freshly",
        )
        entries = read_log_exact(log_file, 4)
        require(
            entries[3].get("operationId") == "Vcenter.VM_list"
            and entries[3].get("vmwareApiSessionId") == new_token,
            "new work did not capture the published replacement session",
        )
        require(
            entries[3].get("rawTarget") == "/api/vcenter/vm"
            and entries[3].get("rawQuery") == "",
            "unset optional VM filters were not omitted",
        )
        require(
            not rotate_future.done()
            and not old_future.done()
            and all(
                item.get("operationId") != "Cis.Session_delete"
                for item in entries
            ),
            "old session was deleted while its request remained in flight",
        )

        release_file.write_text("release\n", encoding="utf-8")
        old_result = old_future.result(timeout=5)
        require(old_result == slow_vms, "old-session request result changed")
        rotate_future.result(timeout=5)
        entries = read_log_exact(log_file, 5)
        require(
            entries[4].get("operationId") == "Cis.Session_delete"
            and entries[4].get("deletingGeneration") == "old"
            and entries[4].get("vmwareApiSessionId") == old_token
            and entries[4].get("releasePresentAtArrival") is True
            and entries[4].get("oldRetirementTooEarly") is False,
            "old session was not retired after its request drained",
        )

        client.close()
        entries = read_log_exact(log_file, 6)
        client.close()
        time.sleep(0.08)
        require(
            len(read_log(log_file)) == 6,
            "idempotent close sent an additional request",
        )
        require(
            entries[5].get("operationId") == "Cis.Session_delete"
            and entries[5].get("deletingGeneration") == "new"
            and entries[5].get("vmwareApiSessionId") == new_token,
            "close did not retire exactly the active replacement session",
        )
        try:
            client.list_vms()
        except RuntimeError:
            pass
        else:
            raise VerificationFailure("closed client accepted new work")
        require(
            len(read_log(log_file)) == 6,
            "closed-client rejection was not local",
        )

        expected_operations = [
            "Cis.Session_create",
            "Vcenter.VM_list",
            "Cis.Session_create",
            "Vcenter.VM_list",
            "Cis.Session_delete",
            "Cis.Session_delete",
        ]
        require(
            [item.get("operationId") for item in entries]
            == expected_operations,
            "operation order or allow-listed route use changed",
        )
        first, slow, replacement, fast, old_delete, new_delete = entries
        require(
            first.get("method") == "POST"
            and first.get("rawTarget") == "/api/session"
            and first.get("authorization")
            == expected_basic(username, old_password)
            and first.get("vmwareApiSessionId") is None
            and first.get("accept") == "application/json",
            "initial session-create wire shape changed",
        )
        require(
            replacement.get("method") == "POST"
            and replacement.get("rawTarget") == "/api/session"
            and replacement.get("authorization")
            == expected_basic(username, new_password)
            and replacement.get("vmwareApiSessionId") is None
            and replacement.get("accept") == "application/json",
            "replacement session-create wire shape changed",
        )
        require_bodyless(first, "initial session create")
        require_bodyless(replacement, "replacement session create")
        require(
            slow.get("method") == "GET"
            and slow.get("rawTarget") == expected_target(slow_filters)
            and slow.get("vmwareApiSessionId") == old_token
            and slow.get("authorization") is None
            and slow.get("accept") == "application/json"
            and slow.get("held") is True,
            "old-session exploded VM query wire shape changed",
        )
        require(
            all(
                f"{name}=" not in slow.get("rawQuery", "")
                for name in ("folders", "hosts", "clusters", "resource_pools")
            ),
            "unset optional filters were serialized on the old request",
        )
        require_bodyless(slow, "old-session VM list")
        require(
            fast.get("method") == "GET"
            and fast.get("authorization") is None
            and fast.get("accept") == "application/json",
            "replacement-session VM list header shape changed",
        )
        require_bodyless(fast, "replacement-session VM list")
        for entry, generation in (
            (old_delete, "old"),
            (new_delete, "new"),
        ):
            require(
                entry.get("method") == "DELETE"
                and entry.get("rawTarget") == "/api/session"
                and entry.get("authorization") is None
                and entry.get("accept") == "application/json"
                and entry.get("deletingGeneration") == generation,
                f"{generation} session-delete wire shape changed",
            )
            require_bodyless(entry, f"{generation} session delete")
    finally:
        try:
            release_file.touch(exist_ok=True)
        except OSError:
            pass
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        executor.shutdown(wait=False, cancel_futures=True)
        stop_process(process)


def verify_failed_rotation_and_validation(package: object, temp: Path) -> None:
    suffix = secrets.token_hex(8)
    username = f"failure-svc-{suffix}"
    old_password = f"old-{secrets.token_urlsafe(12)}"
    configured_new = f"configured-{secrets.token_urlsafe(12)}"
    rejected_password = f"rejected-{secrets.token_urlsafe(12)}"
    old_token = f"old-{secrets.token_urlsafe(16)}"
    new_token = f"new-{secrets.token_urlsafe(16)}"
    error_secret = f"payload-{secrets.token_urlsafe(20)}"
    release_file = temp / "already-released"
    release_file.parent.mkdir(parents=True, exist_ok=True)
    release_file.write_text("released\n", encoding="utf-8")
    fast_vms = [
        {
            "vm": f"vm-{suffix}",
            "name": f"Still old {suffix}",
            "power_state": "POWERED_OFF",
        }
    ]
    scenario = {
        "username": username,
        "old_password": old_password,
        "new_password": configured_new,
        "old_token": old_token,
        "new_token": new_token,
        "error_secret": error_secret,
        "release_file": str(release_file),
        "slow_filters": {"names": [f"unused-{suffix}"]},
        "slow_vms": fast_vms,
        "fast_vms": fast_vms,
    }
    process, port, log_file = start_mock(temp / "mock", scenario)
    client = None
    try:
        client = package.RotatingVcenterClient(
            f"http://127.0.0.1:{port}",
            username,
            old_password,
            timeout=3,
        )
        wait_for_log(log_file, 1)
        for invalid in (None, "", "  ", "bad\rvalue", "bad\nvalue"):
            expect_local_error(client.rotate_password, (invalid,))
        invalid_filters = [
            {"names": []},
            {"names": "not-a-sequence"},
            {"names": [""]},
            {"names": ["same", "same"]},
            {"power_states": ["UNKNOWN"]},
            {"vms": [1]},
        ]
        for kwargs in invalid_filters:
            expect_local_error(client.list_vms, kwargs=kwargs)
        require(
            len(read_log(log_file)) == 1,
            "invalid rotation or filter input made a request",
        )

        try:
            client.rotate_password(rejected_password)
        except package.VcenterError as error:
            require(
                error.operation_id == "Cis.Session_create"
                and error.status_code == 401
                and isinstance(error.payload, dict),
                "failed replacement login lost structured error context",
            )
            rendered = f"{error!s} {error!r}"
            for secret in (
                old_password,
                rejected_password,
                old_token,
                new_token,
                error_secret,
            ):
                require(
                    secret not in rendered,
                    "VcenterError exposed credential, token, or payload text",
                )
        else:
            raise VerificationFailure("rejected replacement login succeeded")

        result = client.list_vms()
        require(result == fast_vms, "failed rotation stranded the old session")
        client.close()
        entries = read_log_exact(log_file, 4)
        require(
            [item.get("operationId") for item in entries]
            == [
                "Cis.Session_create",
                "Cis.Session_create",
                "Vcenter.VM_list",
                "Cis.Session_delete",
            ]
            and entries[2].get("vmwareApiSessionId") == old_token
            and entries[3].get("deletingGeneration") == "old",
            "failed rotation did not leave the old generation active",
        )
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        stop_process(process)


def main() -> int:
    contract = load_object(CONTRACT_PATH)
    sources = load_object(SOURCES_PATH)
    verify_provenance(contract, sources)
    verify_stdlib_only()
    verify_local_constructor_validation(
        importlib.import_module("vcf_session_rotation")
    )

    package = importlib.import_module("vcf_session_rotation")
    require(
        getattr(package, "__all__", None) == PUBLIC_EXPORTS,
        "public export list changed",
    )
    for name in PUBLIC_EXPORTS:
        require(hasattr(package, name), f"missing public export {name}")
    for error_type in (package.VcenterError, package.ProtocolError):
        require(
            issubclass(error_type, RuntimeError),
            f"{error_type.__name__} must remain a RuntimeError",
        )

    with tempfile.TemporaryDirectory(prefix="vcf-session-rotation-") as raw:
        temp = Path(raw)
        verify_primary(package, temp / "primary")
        verify_failed_rotation_and_validation(package, temp / "failure")

    print(
        "PASS: pinned vCenter wire contract, atomic session publication, "
        "old-generation drain, exact optional omission, and retirement verified"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationFailure as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
