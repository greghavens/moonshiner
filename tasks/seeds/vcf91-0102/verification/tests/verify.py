#!/usr/bin/env python3
"""Protected deterministic verification for the retry-safe CPU assignment."""

from __future__ import annotations

import ast
import importlib
import json
import math
import os
import secrets
import subprocess
import sys
import tempfile
import time
from dataclasses import FrozenInstanceError
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / "tools" / "mock_vcenter.py"
PACKAGE_ROOT = ROOT / "vcf_cpu_retry"
OPERATION_ID = "Vcenter.Vm.Hardware.Cpu_update"
COMMIT_SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_BLOB_SHA = "8028b0824c4ff3503d05f44814f967938a795c40"
SPEC_PATH = "specifications/vsphere/openapi/automation/vcenter.yaml"
PUBLIC_EXPORTS = [
    "CpuUpdateClient",
    "CpuUpdateResult",
    "VcenterError",
    "ProtocolError",
    "RetryExhaustedError",
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


def verify_provenance(contract: dict, sources: dict) -> None:
    source = contract.get("source")
    require(isinstance(source, dict), "contract source metadata is missing")
    require(
        source.get("kind") == "pinned-openapi-specification"
        and source.get("repository") == "vmware/vcf-api-specs"
        and source.get("commitSha") == COMMIT_SHA
        and source.get("specPath") == SPEC_PATH
        and source.get("specBlobSha") == SPEC_BLOB_SHA
        and source.get("license") == "Apache-2.0"
        and source.get("openapi") == "3.0.3"
        and source.get("apiVersion") == "9.1.0.0"
        and source.get("serverTemplate") == "https://{host}/api"
        and source.get("basePath") == "/api",
        "contract is not pinned to the required vSphere specification",
    )
    require(
        contract.get("securitySchemes", {}).get("api_key_auth")
        == {
            "type": "apiKey",
            "in": "header",
            "name": "vmware-api-session-id",
        },
        "contract security scheme changed",
    )

    operations = contract.get("operations")
    require(
        isinstance(operations, list) and len(operations) == 1,
        "contract must project exactly one operation",
    )
    operation = operations[0]
    require(
        operation.get("operationId") == OPERATION_ID
        and operation.get("method") == "PATCH"
        and operation.get("specPathItem")
        == "/vcenter/vm/{vm}/hardware/cpu"
        and operation.get("path")
        == "/api/vcenter/vm/{vm}/hardware/cpu"
        and operation.get("security") == ["api_key_auth"],
        "focused operation projection changed",
    )
    require(
        operation.get("requestBody")
        == {
            "required": True,
            "contentType": "application/json",
            "schema": "Vcenter.Vm.Hardware.Cpu.UpdateSpec",
        },
        "focused request-body projection changed",
    )
    require(
        operation.get("responses", {}).get("204") == {"content": False},
        "focused success response projection changed",
    )

    schema = contract.get("schemas", {}).get(
        "Vcenter.Vm.Hardware.Cpu.UpdateSpec"
    )
    require(
        isinstance(schema, dict)
        and schema.get("type") == "object"
        and schema.get("required") == [],
        "CPU UpdateSpec projection changed",
    )
    properties = schema.get("properties")
    require(
        isinstance(properties, dict)
        and list(properties)
        == [
            "count",
            "cores_per_socket",
            "hot_add_enabled",
            "hot_remove_enabled",
        ],
        "CPU UpdateSpec property projection changed",
    )
    require(
        properties["count"]
        == {
            "type": "integer",
            "format": "int64",
            "required": False,
            "unsetBehavior": "unchanged",
        },
        "CPU count projection changed",
    )
    for optional_name in (
        "cores_per_socket",
        "hot_add_enabled",
        "hot_remove_enabled",
    ):
        require(
            properties[optional_name].get("required") is False
            and properties[optional_name].get("unsetBehavior") == "unchanged",
            f"{optional_name} must remain an optional unchanged-on-unset field",
        )

    require(
        sources.get("repository") == "vmware/vcf-api-specs"
        and sources.get("repositoryCommitSha") == COMMIT_SHA
        and sources.get("specPath") == SPEC_PATH
        and sources.get("specBlobSha") == SPEC_BLOB_SHA
        and sources.get("license") == "Apache-2.0"
        and sources.get("operationIds") == [OPERATION_ID],
        "official source summary is not pinned",
    )
    source_operations = sources.get("operations")
    require(
        isinstance(source_operations, list) and len(source_operations) == 1,
        "official source operation records are incomplete",
    )
    source_operation = source_operations[0]
    require(
        source_operation.get("operationId") == OPERATION_ID
        and source_operation.get("repositoryCommitSha") == COMMIT_SHA
        and source_operation.get("specPath") == SPEC_PATH,
        "official source operation record is not independently pinned",
    )


def verify_stdlib_only() -> None:
    require(PACKAGE_ROOT.is_dir(), "vcf_cpu_retry package is missing")
    python_files = sorted(PACKAGE_ROOT.rglob("*.py"))
    require(python_files, "vcf_cpu_retry contains no Python files")
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
                        root_name in stdlib or root_name == "vcf_cpu_retry",
                        f"{path} imports non-stdlib module {root_name}",
                    )
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                root_name = (node.module or "").split(".", 1)[0]
                require(
                    root_name in stdlib or root_name == "vcf_cpu_retry",
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


def read_log_exact(log_file: Path, expected_count: int) -> list[dict]:
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if log_file.exists():
            lines = [
                line
                for line in log_file.read_text(encoding="utf-8").splitlines()
                if line
            ]
            if len(lines) >= expected_count:
                time.sleep(0.05)
                final_lines = [
                    line
                    for line in log_file.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if line
                ]
                require(
                    len(final_lines) == expected_count,
                    f"expected {expected_count} requests, "
                    f"observed {len(final_lines)}",
                )
                return [json.loads(line) for line in final_lines]
        time.sleep(0.01)
    raise VerificationFailure(
        f"request log did not reach {expected_count} entries"
    )


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def start_mock(temp: Path, scenario: dict) -> tuple[subprocess.Popen[str], int, Path]:
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


def verify_local_validation(package: object, token: str) -> None:
    client_type = package.CpuUpdateClient
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
            (base_url, token),
        )
    for invalid_token in (
        None,
        "",
        "   ",
        "bad\rvalue",
        "bad\nvalue",
        "not-latin1-\u2603",
    ):
        expect_local_error(
            client_type,
            ("http://127.0.0.1:1", invalid_token),
        )
    for timeout in (True, 0, -1, math.inf, -math.inf, math.nan):
        expect_local_error(
            client_type,
            ("http://127.0.0.1:1", token),
            {"timeout": timeout},
        )

    client = client_type("http://127.0.0.1:1/", token, timeout=0.25)
    for vm in (None, "", " \t "):
        expect_local_error(client.set_cpu_count, (vm, 2))
    for count in (None, True, False, 0, -1, 2**63):
        expect_local_error(client.set_cpu_count, ("vm-local", count))


def expected_wire(scenario: dict) -> tuple[str, bytes]:
    target = (
        "/api/vcenter/vm/"
        + quote(scenario["vm"], safe="")
        + "/hardware/cpu"
    )
    body = json.dumps(
        {"count": scenario["desired_count"]},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return target, body


def verify_wire(
    entries: list[dict],
    scenario: dict,
    expected_actions: list[str],
    expected_statuses: list[int | None],
) -> None:
    target, body = expected_wire(scenario)
    require(
        [entry["requestIndex"] for entry in entries]
        == list(range(len(entries))),
        "request log sequence changed",
    )
    for index, entry in enumerate(entries):
        require(
            entry["operationId"] == OPERATION_ID,
            f"request {index + 1} used an operation outside the contract",
        )
        require(
            entry["method"] == "PATCH"
            and entry["rawTarget"] == target
            and entry["rawPath"] == target
            and entry["rawQuery"] == "",
            f"request {index + 1} method or raw target is incorrect",
        )
        require(
            entry["vmwareApiSessionId"] == scenario["session_token"]
            and entry["authorization"] is None
            and "authorization" not in entry["headerNames"],
            f"request {index + 1} authentication header is incorrect",
        )
        require(
            entry["accept"] == "application/json"
            and entry["contentType"] == "application/json"
            and entry["transferEncoding"] is None,
            f"request {index + 1} media or transfer headers are incorrect",
        )
        require(
            entry["declaredContentLength"] == len(body)
            and entry["bodyLength"] == len(body)
            and entry["bodyHex"] == body.hex(),
            f"request {index + 1} body bytes are incorrect",
        )
        require(
            entry["bodyJson"] == {"count": scenario["desired_count"]}
            and list(entry["bodyJson"]) == ["count"],
            f"request {index + 1} must omit all unset CPU fields",
        )
    require(
        [entry["responseAction"] for entry in entries] == expected_actions,
        "mock did not observe the required response-loss sequence",
    )
    require(
        [entry["status"] for entry in entries] == expected_statuses,
        "mock statuses do not match the scenario",
    )
    if len(entries) == 2:
        require(
            entries[0]["rawTarget"] == entries[1]["rawTarget"]
            and entries[0]["bodyHex"] == entries[1]["bodyHex"]
            and entries[0]["vmwareApiSessionId"]
            == entries[1]["vmwareApiSessionId"]
            and entries[0]["accept"] == entries[1]["accept"]
            and entries[0]["contentType"] == entries[1]["contentType"],
            "the retry is not wire-identical to the ambiguous first attempt",
        )


def run_success_scenario(package: object, scenario: dict, temp: Path) -> None:
    process, port, log_file = start_mock(temp, scenario)
    saved_proxy_env = {
        name: os.environ.get(name)
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY")
    }
    try:
        client = package.CpuUpdateClient(
            f"http://127.0.0.1:{port}/",
            scenario["session_token"],
            timeout=2.0,
        )
        require(
            log_file.read_text(encoding="utf-8") == "",
            "client construction performed an HTTP request",
        )
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:9"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9"
        os.environ["NO_PROXY"] = ""
        result = client.set_cpu_count(
            scenario["vm"], scenario["desired_count"]
        )
        entries = read_log_exact(log_file, 2)
    finally:
        for name, value in saved_proxy_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        stop_process(process)

    require(
        isinstance(result, package.CpuUpdateResult),
        "set_cpu_count must return CpuUpdateResult",
    )
    require(
        (
            result.vm,
            result.count,
            result.attempts,
            result.operation_id,
        )
        == (
            scenario["vm"],
            scenario["desired_count"],
            2,
            OPERATION_ID,
        ),
        "success result fields are incorrect",
    )
    try:
        result.attempts = 99
    except (FrozenInstanceError, AttributeError):
        pass
    else:
        raise VerificationFailure("CpuUpdateResult must be immutable")

    verify_wire(
        entries,
        scenario,
        ["disconnect", "response"],
        [None, 204],
    )
    require(
        [entry["appliedChange"] for entry in entries] == [True, False]
        and [entry["effectCount"] for entry in entries] == [1, 1]
        and [entry["currentCount"] for entry in entries]
        == [scenario["desired_count"], scenario["desired_count"]],
        "the retry duplicated or lost the desired-state mutation",
    )


def run_exhaustion_scenario(package: object, scenario: dict, temp: Path) -> None:
    process, port, log_file = start_mock(temp, scenario)
    error = None
    try:
        client = package.CpuUpdateClient(
            f"http://127.0.0.1:{port}",
            scenario["session_token"],
            timeout=2.0,
        )
        try:
            client.set_cpu_count(
                scenario["vm"], scenario["desired_count"]
            )
        except package.RetryExhaustedError as caught:
            error = caught
        entries = read_log_exact(log_file, 2)
    finally:
        stop_process(process)

    require(error is not None, "two lost responses must exhaust the retry")
    require(
        error.operation_id == OPERATION_ID and error.attempts == 2,
        "RetryExhaustedError metadata is incorrect",
    )
    rendered = f"{error!s}\n{error!r}"
    require(
        scenario["session_token"] not in rendered
        and scenario["error_secret"] not in rendered
        and "RemoteDisconnected" not in rendered
        and "ConnectionReset" not in rendered,
        "retry error text exposes protected transport or session data",
    )
    verify_wire(
        entries,
        scenario,
        ["disconnect", "disconnect"],
        [None, None],
    )
    require(
        [entry["appliedChange"] for entry in entries] == [True, False]
        and [entry["effectCount"] for entry in entries] == [1, 1],
        "retry exhaustion produced more than one state transition",
    )


def run_http_error_scenario(package: object, scenario: dict, temp: Path) -> None:
    process, port, log_file = start_mock(temp, scenario)
    error = None
    try:
        client = package.CpuUpdateClient(
            f"http://127.0.0.1:{port}",
            scenario["session_token"],
            timeout=2.0,
        )
        try:
            client.set_cpu_count(
                scenario["vm"], scenario["desired_count"]
            )
        except package.VcenterError as caught:
            error = caught
        entries = read_log_exact(log_file, 1)
    finally:
        stop_process(process)

    require(error is not None, "HTTP 503 must raise VcenterError")
    require(
        type(error) is package.VcenterError,
        "HTTP 503 must not be reported as a retry/protocol error",
    )
    require(
        error.operation_id == OPERATION_ID
        and error.status_code == 503
        and isinstance(error.payload, dict)
        and error.payload.get("error_type") == "SERVICE_UNAVAILABLE",
        "VcenterError metadata or decoded payload is incorrect",
    )
    rendered = f"{error!s}\n{error!r}"
    require(
        scenario["session_token"] not in rendered
        and scenario["error_secret"] not in rendered
        and repr(error.payload) not in rendered,
        "HTTP error text exposes the session or response payload",
    )
    verify_wire(entries, scenario, ["response"], [503])
    require(
        entries[0]["effectCount"] == 0
        and entries[0]["currentCount"] == scenario["initial_count"],
        "HTTP failure must not report a successful mutation",
    )


def main() -> int:
    contract = load_object(CONTRACT_PATH)
    sources = load_object(SOURCES_PATH)
    verify_provenance(contract, sources)
    verify_stdlib_only()

    sys.path.insert(0, str(ROOT))
    package = importlib.import_module("vcf_cpu_retry")
    require(
        package.__all__ == PUBLIC_EXPORTS,
        "vcf_cpu_retry public exports changed",
    )
    for name in PUBLIC_EXPORTS:
        require(
            hasattr(package, name),
            f"vcf_cpu_retry does not expose {name}",
        )

    token = f"session-{secrets.token_urlsafe(24)}"
    verify_local_validation(package, token)

    nonce = secrets.token_hex(8)
    initial_count = 2 + secrets.randbelow(8)
    desired_count = initial_count + 2 + secrets.randbelow(8)
    common = {
        "vm": f"vm/{nonce} snow \u03b2?#%",
        "session_token": token,
        "initial_count": initial_count,
        "desired_count": desired_count,
        "error_secret": f"server-detail-{secrets.token_hex(10)}",
    }

    with tempfile.TemporaryDirectory(prefix="vcf91-0102-") as temp_text:
        temp = Path(temp_text)
        success = dict(common, behavior="disconnect_once")
        run_success_scenario(package, success, temp / "success")

        exhaustion = dict(
            common,
            vm=f"vm/{secrets.token_hex(7)} retry \u03bb?#%",
            initial_count=desired_count,
            desired_count=desired_count + 2,
            behavior="disconnect_always",
        )
        run_exhaustion_scenario(package, exhaustion, temp / "exhaustion")

        http_error = dict(
            common,
            vm=f"vm/{secrets.token_hex(7)} http \u03c0?#%",
            initial_count=desired_count + 2,
            desired_count=desired_count + 4,
            behavior="http_error",
        )
        run_http_error_scenario(package, http_error, temp / "http-error")

    print(
        "PASS: contract provenance, exact retry wire shape, optional omission, "
        "one-effect replay, exhaustion, and non-retryable HTTP status"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerificationFailure as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
