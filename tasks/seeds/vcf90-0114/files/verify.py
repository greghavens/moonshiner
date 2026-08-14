#!/usr/bin/env python3
"""Deterministic acceptance verifier for the VCF Installer client exercise."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PROTECTED_HASHES = {
    "TestMain.java": "31276d69364720560a2776b671d9cba9c6830c82ead1dcbd18d69756ac306760",
    "mock_server.py": "c5ba62e45b7c055867eedb7b8f82435088aa05f244328678e86950c87e9aad39",
    "docs/contract.json": "20ce1f708e5f299bc79f1ebf5c0bbe22506faa309853d01cfe1fb045c2c8b6ca",
    "docs/official_sources.json": "b7942855a78efc1fe2b2195e2960490313f4cc7a35bd1a2d3ee3cb527b7cb981",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_protected_files() -> None:
    for relative, expected in PROTECTED_HASHES.items():
        actual = sha256(ROOT / relative)
        if actual != expected:
            raise AssertionError(f"protected fixture changed: {relative}")


def wait_for_server(port_file: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"mock failed to start\nstdout={stdout}\nstderr={stderr}")
        if port_file.exists():
            port = int(port_file.read_text(encoding="ascii"))
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    return port
            except OSError:
                pass
        time.sleep(0.02)
    raise AssertionError("mock did not become ready")


def expect_headers(entry: dict, *, authorization: str | None, has_body: bool) -> None:
    headers = entry["headers"]
    if headers.get("accept") != "application/json":
        raise AssertionError(f"wrong Accept header: {headers!r}")
    if authorization is not None and headers.get("authorization") != authorization:
        raise AssertionError(f"wrong Authorization header: {headers!r}")
    if has_body:
        if headers.get("content-type") != "application/json":
            raise AssertionError(f"wrong Content-Type header: {headers!r}")
    elif "content-type" in headers:
        raise AssertionError(f"GET must not send Content-Type: {headers!r}")


def assert_wire_log(log_path: Path, scenario: str) -> None:
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    full_sequence = [
        ("POST", "/v1/tokens", "createToken"),
        ("GET", "/v1/tasks", "getTasks"),
        ("GET", "/v1/tasks", "getTasks"),
        ("PATCH", "/v1/tokens/access-token/refresh", "refreshAccessToken"),
        ("GET", "/v1/tasks", "getTasks"),
        ("GET", "/v1/tasks", "getTasks"),
    ]
    expected_count = {
        "happy": 6,
        "create-status": 1,
        "create-malformed": 1,
        "tasks-status": 2,
        "tasks-malformed": 2,
        "metadata-malformed": 2,
        "refresh-status": 4,
        "refresh-malformed": 4,
    }[scenario]
    if len(entries) != expected_count:
        raise AssertionError(
            f"{scenario}: expected exactly {expected_count} requests, got {len(entries)}: "
            f"{entries!r}"
        )

    for index, (entry, (method, path, operation_id)) in enumerate(
        zip(entries, full_sequence), 1
    ):
        if entry["sequence"] != index:
            raise AssertionError(f"request sequence is not contiguous: {entries!r}")
        actual = (entry["method"], entry["path"], entry["operationId"])
        if actual != (method, path, operation_id):
            raise AssertionError(f"request {index} wire target differs: {actual!r}")

    login = entries[0]
    expected_login = {
        "username": "administrator@vsphere.local",
        "password": "P@ss\"word\\one\nline\t\u0001",
    }
    if json.loads(login["body"]) != expected_login:
        raise AssertionError("unset TokenCreationSpec fields must be omitted")
    if login["query"]:
        raise AssertionError(f"createToken must not send query parameters: {login['query']!r}")
    expect_headers(login, authorization=None, has_body=True)

    for index, entry in enumerate(entries[1:], 1):
        if entry["operationId"] == "getTasks":
            expected_page = "0" if index == 1 else "2" if index == 5 else "1"
            if len(entry["query"]) != 2 or dict(entry["query"]) != {
                "pageNumber": expected_page,
                "pageSize": "2",
            }:
                raise AssertionError(f"wrong page or optional query present: {entry['query']!r}")
            authorization = (
                "Bearer access-token-2" if index in (4, 5) else "Bearer access-token-1"
            )
            expect_headers(entry, authorization=authorization, has_body=False)
            if entry["body"] != "":
                raise AssertionError("getTasks must not send a request body")
        else:
            if json.loads(entry["body"]) != "refresh-token-1":
                raise AssertionError(f"refresh body must be a JSON string: {entry['body']!r}")
            if entry["query"]:
                raise AssertionError(
                    f"refreshAccessToken must not send query parameters: {entry['query']!r}"
                )
            expect_headers(entry, authorization=None, has_body=True)

    for entry in entries[1:]:
        if entry["operationId"] == "getTasks" and {
            pair[0] for pair in entry["query"]
        } != {"pageNumber", "pageSize"}:
            raise AssertionError(f"unset optional query fields must be omitted: {entry['query']!r}")


def run_scenario(classes: Path, temporary: Path, scenario: str) -> None:
    log_path = temporary / f"{scenario}.jsonl"
    port_file = temporary / f"{scenario}.port"
    server = subprocess.Popen(
        [
            sys.executable, str(ROOT / "mock_server.py"),
            "--contract", str(ROOT / "docs" / "contract.json"),
            "--log", str(log_path),
            "--port-file", str(port_file),
            "--scenario", scenario,
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    try:
        port = wait_for_server(port_file, server)
        run_result = subprocess.run(
            [
                "java", "-cp", str(classes), "TestMain",
                f"http://127.0.0.1:{port}", scenario,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
    finally:
        server.terminate()
        try:
            server.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()
            server.communicate(timeout=3)

    if run_result.returncode != 0:
        raise AssertionError(
            f"TestMain failed for {scenario}\n"
            f"stdout={run_result.stdout}\nstderr={run_result.stderr}"
        )
    expected_output = (
        "TASK_IDS=task-001,task-002,task-003,task-004,task-005"
        if scenario == "happy"
        else f"EXPECTED_FAILURE={scenario}"
    )
    if run_result.stdout.strip() != expected_output:
        raise AssertionError(f"unexpected {scenario} output: {run_result.stdout!r}")
    assert_wire_log(log_path, scenario)


def main() -> int:
    assert_protected_files()
    java_files = {path.name for path in ROOT.glob("*.java")}
    if java_files != {"VcfInstallerClient.java", "TestMain.java"}:
        raise AssertionError(f"client must remain a single production Java file: {java_files!r}")

    with tempfile.TemporaryDirectory(prefix="vcf-installer-test-") as directory:
        temporary = Path(directory)
        classes = temporary / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            [
                "javac", "--release", "17", "-classpath", str(classes),
                "-d", str(classes),
                str(ROOT / "VcfInstallerClient.java"), str(ROOT / "TestMain.java"),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        if compile_result.returncode != 0:
            raise AssertionError(
                f"javac failed\nstdout={compile_result.stdout}\nstderr={compile_result.stderr}"
            )

        for scenario in (
            "happy",
            "create-status",
            "create-malformed",
            "tasks-status",
            "tasks-malformed",
            "metadata-malformed",
            "refresh-status",
            "refresh-malformed",
        ):
            run_scenario(classes, temporary, scenario)

    print("PASS: VCF Installer token refresh and exact wire contract verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
