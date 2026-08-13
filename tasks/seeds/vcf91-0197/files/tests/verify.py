#!/usr/bin/env python3
"""Protected, deterministic acceptance verifier for vcf91-0197."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TASK_ID = "7d5de5c8-e8d0-4a38-a61d-0eef8917db51"
TASK_IDS = [
    TASK_ID,
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
    "44444444-4444-4444-8444-444444444444",
    "55555555-5555-4555-8555-555555555555",
    "66666666-6666-4666-8666-666666666666",
    "77777777-7777-4777-8777-777777777777",
]
SDK_VERSION = "13.5.0.25380678"
SOURCE_SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SOURCE_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"
EXPECTED_OPERATIONS = {
    "createToken": ("POST", "/v1/tokens"),
    "getApplianceInfo": ("GET", "/v1/system/appliance-info"),
    "updateProxyConfiguration": ("PATCH", "/v1/system/proxy-configuration"),
    "getTask": ("GET", "/v1/tasks/{id}"),
}


def fail(message: str) -> None:
    raise AssertionError(message)


def verify_contract() -> None:
    contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "docs/official_sources.json").read_text(encoding="utf-8"))

    if contract["apiVersion"] != "9.1.0.0":
        fail("contract API version is not VCF Installer 9.1.0.0")
    if contract["derivedFrom"]["commitSha"] != SOURCE_SHA:
        fail("contract source commit changed")
    if contract["derivedFrom"]["specPath"] != SOURCE_PATH:
        fail("contract source path changed")
    actual = {
        operation_id: (definition["method"], definition["path"])
        for operation_id, definition in contract["operations"].items()
    }
    if actual != EXPECTED_OPERATIONS:
        fail(f"contract operation map changed: {actual!r}")
    if contract["operationIds"] != list(EXPECTED_OPERATIONS):
        fail("contract operationId order or contents changed")

    proxy = contract["schemas"]["ProxyConfiguration"]
    expected_proxy_properties = {
        "isConfigured",
        "isEnabled",
        "host",
        "port",
        "transferProtocol",
        "username",
        "password",
        "isAuthenticated",
    }
    if set(proxy["properties"]) != expected_proxy_properties or proxy["required"] != []:
        fail("ProxyConfiguration no longer matches the pinned OpenAPI schema")
    if proxy["properties"]["isConfigured"].get("readOnly") is not True:
        fail("isConfigured must remain read-only")
    task = contract["schemas"]["Task"]
    expected_task_properties = {
        "id",
        "name",
        "localizableDescriptionPack",
        "type",
        "status",
        "creationTimestamp",
        "completionTimestamp",
        "subTasks",
        "errors",
        "resources",
        "resolutionStatus",
        "isCancellable",
        "isRetryable",
    }
    if set(task["properties"]) != expected_task_properties:
        fail("Task direct fields no longer match the pinned OpenAPI schema")
    if set(task["required"]) != {"creationTimestamp", "id", "name", "status"}:
        fail("Task required fields no longer match the pinned OpenAPI schema")
    expected_terminal = {
        "SUCCESSFUL",
        "FAILED",
        "CANCELLED",
        "COMPLETED_WITH_WARNING",
        "SKIPPED",
        "TIMED_OUT",
    }
    if set(contract["polling"]["terminalStatuses"]) != expected_terminal:
        fail("terminal task status contract changed")
    if set(contract["polling"]["nonTerminalStatuses"]) != {
        "PENDING",
        "IN_PROGRESS",
        "QUEUED",
    }:
        fail("non-terminal task status contract changed")

    if sources["commitSha"] != SOURCE_SHA or sources["specPath"] != SOURCE_PATH:
        fail("official source provenance changed")
    if sources["repositoryLicense"] != "Apache-2.0":
        fail("official source license changed")
    source_operations = {
        row["operationId"]: (row["method"], row["path"])
        for row in sources["operations"]
    }
    if source_operations != EXPECTED_OPERATIONS:
        fail("official_sources.json must record every exact operationId")
    if SOURCE_SHA not in sources["specUrl"] or SOURCE_PATH not in sources["specUrl"]:
        fail("official spec URL is not commit-pinned")


def verify_candidate_shape() -> None:
    module_path = ROOT / "src/VcfInstaller.Proxy.psm1"
    module = module_path.read_text(encoding="utf-8")
    manifest = (ROOT / "src/VcfInstaller.Proxy.psd1").read_text(encoding="utf-8")

    required_commands = {
        "Initialize-VcfInstallerProxyConfiguration",
        "Invoke-VcfInstallerUpdateProxyConfiguration",
        "Invoke-VcfInstallerGetTask",
    }
    missing = sorted(command for command in required_commands if command not in module)
    if missing:
        fail("implementation does not use required VMware SDK cmdlets: " + ", ".join(missing))
    forbidden = ["Invoke-RestMethod", "Invoke-WebRequest", "System.Net.Http", "HttpClient"]
    used_forbidden = [token for token in forbidden if token.lower() in module.lower()]
    if used_forbidden:
        fail("direct HTTP clients are not allowed: " + ", ".join(used_forbidden))
    if "Export-ModuleMember -Function Set-VcfInstallerProxyConfiguration" not in module:
        fail("public function export changed")
    if "VMware.Sdk.Vcf.Installer" not in manifest or SDK_VERSION not in manifest:
        fail("module manifest does not pin the provided VMware SDK prerequisite")


def read_log(log_path: Path) -> list[dict[str, object]]:
    records = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if line:
            records.append(json.loads(line))
    return records


def assert_known_route(record: dict[str, object]) -> None:
    method = record["method"]
    path = record["path"]
    known = {
        ("POST", "/v1/tokens"),
        ("GET", "/v1/system/appliance-info"),
        ("PATCH", "/v1/system/proxy-configuration"),
    }
    if (method, path) in known:
        return
    if method == "GET" and isinstance(path, str) and path.startswith("/v1/tasks/"):
        return
    fail(f"request used an operation outside docs/contract.json: {method} {path}")


def verify_wire(records: list[dict[str, object]]) -> None:
    if not records:
        fail("loopback mock received no requests")
    for record in records:
        assert_known_route(record)

    patches = [
        record
        for record in records
        if record["method"] == "PATCH"
        and record["path"] == "/v1/system/proxy-configuration"
    ]
    expected_bodies = [
        {
            "isEnabled": True,
            "host": "proxy.example.com",
            "port": 3128,
            "transferProtocol": "HTTPS",
            "isAuthenticated": False,
        },
        {"isEnabled": False},
        {
            "isEnabled": True,
            "host": "authenticated.example.com",
            "port": 8080,
            "transferProtocol": "HTTP",
            "username": "proxy-user",
            "password": "proxy-password",
            "isAuthenticated": True,
        },
        {"isEnabled": False, "host": "cancelled.example.com"},
        {"isEnabled": False, "host": "skipped.example.com"},
        {"isEnabled": False, "host": "timed-out-task.example.com"},
        {"isEnabled": False, "host": "unexpected.example.com"},
        {"isEnabled": False, "host": "poll-timeout.example.com"},
    ]
    if len(patches) != len(expected_bodies):
        fail(
            f"expected {len(expected_bodies)} proxy PATCH requests, "
            f"received {len(patches)}"
        )
    for index, (patch, expected_body) in enumerate(zip(patches, expected_bodies)):
        if patch["query"] != "":
            fail(f"proxy PATCH {index} must not have a query string")
        try:
            body = json.loads(str(patch["body"]))
        except json.JSONDecodeError as error:
            fail(f"proxy PATCH {index} body is not JSON: {error}")
        if body != expected_body:
            fail(
                f"proxy PATCH {index} has the wrong exact property set or values: "
                f"{body!r}"
            )
        if "isConfigured" in body:
            fail(f"read-only field 'isConfigured' was sent by proxy PATCH {index}")

        headers = patch["headers"]
        assert isinstance(headers, dict)
        content_type = (
            str(headers.get("content-type", ""))
            .split(";", 1)[0]
            .strip()
            .lower()
        )
        if content_type != "application/json":
            fail(
                f"proxy PATCH {index} Content-Type is not application/json: "
                f"{content_type!r}"
            )
        accept = str(headers.get("accept", "")).lower()
        if "application/json" not in accept:
            fail(f"proxy PATCH {index} does not accept application/json: {accept!r}")
        if headers.get("authorization") != "Bearer loopback-access-token":
            fail(f"proxy PATCH {index} did not use the SDK connection bearer token")

    expected_poll_counts = [2, 1, 3, 1, 1, 1, 1, 1]
    for task_id, expected_count in zip(TASK_IDS, expected_poll_counts):
        task_path = f"/v1/tasks/{task_id}"
        task_gets = [
            record
            for record in records
            if record["method"] == "GET" and record["path"] == task_path
        ]
        if len(task_gets) != expected_count:
            fail(
                f"task {task_id} expected {expected_count} exact-id polls, "
                f"received {len(task_gets)}"
            )
        for record in task_gets:
            if record["query"] != "" or record["body"] != "":
                fail("getTask poll must have no query string or request body")
            poll_headers = record["headers"]
            assert isinstance(poll_headers, dict)
            if poll_headers.get("authorization") != "Bearer loopback-access-token":
                fail("getTask poll did not use the SDK connection bearer token")

    application_requests = [
        (record["method"], record["path"])
        for record in records
        if record["method"] == "PATCH"
        or (record["method"] == "GET" and str(record["path"]).startswith("/v1/tasks/"))
    ]
    expected_application_requests: list[tuple[object, object]] = []
    for task_id, poll_count in zip(TASK_IDS, expected_poll_counts):
        expected_application_requests.append(
            ("PATCH", "/v1/system/proxy-configuration")
        )
        expected_application_requests.extend(
            [("GET", f"/v1/tasks/{task_id}")] * poll_count
        )
    if application_requests != expected_application_requests:
        fail(f"asynchronous request sequence is wrong: {application_requests!r}")


def run_integration() -> None:
    pwsh = shutil.which("pwsh")
    if not pwsh:
        fail("pwsh is required by this PowerShell task")

    with tempfile.TemporaryDirectory(prefix="vcf91-0197-") as temp_name:
        temp = Path(temp_name)
        log_path = temp / "requests.jsonl"
        port_path = temp / "port"
        output_path = temp / "result.json"
        environment = os.environ.copy()
        environment["NO_PROXY"] = "127.0.0.1,localhost"
        environment["no_proxy"] = "127.0.0.1,localhost"
        environment["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
        environment["POWERSHELL_UPDATECHECK"] = "Off"
        server = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "tests/mock_vcf_installer.py"),
                "--contract",
                str(ROOT / "docs/contract.json"),
                "--log",
                str(log_path),
                "--port-file",
                str(port_path),
            ],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while not port_path.exists() and time.monotonic() < deadline:
                if server.poll() is not None:
                    stdout, stderr = server.communicate(timeout=1)
                    fail(f"mock exited during startup\nstdout: {stdout}\nstderr: {stderr}")
                time.sleep(0.02)
            if not port_path.exists():
                fail("mock did not publish its loopback port")
            port = int(port_path.read_text(encoding="ascii"))

            completed = subprocess.run(
                [
                    pwsh,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(ROOT / "tests/exercise.ps1"),
                    "-Port",
                    str(port),
                    "-OutputFile",
                    str(output_path),
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=45,
            )
            if completed.returncode != 0:
                fail(
                    "PowerShell integration failed\n"
                    f"stdout:\n{completed.stdout}\n"
                    f"stderr:\n{completed.stderr}"
                )
            if not output_path.exists():
                fail("PowerShell integration did not write a result")
            result = json.loads(output_path.read_text(encoding="utf-8-sig"))
            expected_result = {
                "firstTask": {
                    "id": TASK_ID,
                    "name": "Update proxy configuration",
                    "status": "SUCCESSFUL",
                },
                "terminalStatuses": [
                    "SUCCESSFUL",
                    "COMPLETED_WITH_WARNING",
                    "FAILED",
                    "CANCELLED",
                    "SKIPPED",
                    "TIMED_OUT",
                ],
                "unexpectedRejected": True,
                "timeoutException": "System.TimeoutException",
            }
            if result != expected_result:
                fail(f"polling outcomes are wrong: {result!r}")
            verify_wire(read_log(log_path))
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=3)


def main() -> None:
    verify_contract()
    verify_candidate_shape()
    run_integration()
    print("PASS: VCF Installer proxy update used the pinned wire contract and terminal polling")


if __name__ == "__main__":
    try:
        main()
    except (
        AssertionError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        subprocess.TimeoutExpired,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
