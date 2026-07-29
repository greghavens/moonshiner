#!/usr/bin/env python3
"""Deterministic protected verifier for VcfBackupClient."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote


PROJECT = Path(__file__).resolve().parents[1]
PROTECTED_HASHES = {
    "docs/contract.json": "d30b4c999b21627059979a08342f9f402f0fd4ded1f80c24fb1c2ec04b765f48",
    "docs/official_sources.json": "acbb84a28e8e2baaf58a638f5a0254f6c05f23be94dc2b7815e4b0e1df081f70",
    "tests/TestMain.java": "77ffbc50b59ccb0295bb715ac28a5b0d41fe12a60ca379d5da056f81ede730dc",
    "tests/mock_server.py": "9f19a3430f2737ab3aced7a84fba1ba6726fdc629b5460bdb57ad916058524b8",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def check_protected_files() -> None:
    for relative, expected in PROTECTED_HASHES.items():
        actual = hashlib.sha256((PROJECT / relative).read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected fixture changed: {relative}")


def check_contract_metadata() -> None:
    contract = json.loads((PROJECT / "docs/contract.json").read_text(encoding="utf-8"))
    source = json.loads((PROJECT / "docs/official_sources.json").read_text(encoding="utf-8"))
    sha = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
    spec_path = "specifications/sddc-manager/sddc-manager-openapi.json"
    expected_operations = {
        ("updateBackupConfiguration", "PATCH", "/v1/system/backup-configuration"),
        ("getTask", "GET", "/v1/tasks/{id}"),
    }
    if (
        contract["derived_from"]["repository_commit_sha"] != sha
        or source["repository"]["commit_sha"] != sha
    ):
        fail("official source commit is not pinned")
    if (
        contract["derived_from"]["spec_path"] != spec_path
        or source["specification"]["path"] != spec_path
    ):
        fail("official specification path changed")
    projected = {
        (item["operationId"], item["method"], item["path"])
        for item in contract["operations"]
    }
    recorded = {
        (item["operationId"], item["method"], item["path"])
        for item in source["operations"]
    }
    if projected != expected_operations or recorded != expected_operations:
        fail("operationId source record changed")


def wait_for_server(port_file: Path, process: subprocess.Popen[str]) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            fail(f"mock exited during startup\nstdout={stdout}\nstderr={stderr}")
        if port_file.exists() and port_file.read_text(encoding="utf-8").strip():
            return json.loads(port_file.read_text(encoding="utf-8"))
        time.sleep(0.02)
    fail("mock did not publish its loopback port")


def check_wire_log(log_path: Path, server_info: dict) -> None:
    entries = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(entries) != 3:
        fail(f"expected one PATCH and two task polls, got {len(entries)} requests")

    patch, first_poll, second_poll = entries
    if [entry["sequence"] for entry in entries] != [1, 2, 3]:
        fail("request sequence is not deterministic")
    if (
        patch["operationId"],
        patch["method"],
        patch["target"],
        patch["path"],
        patch["query"],
    ) != (
        "updateBackupConfiguration",
        "PATCH",
        "/v1/system/backup-configuration",
        "/v1/system/backup-configuration",
        "",
    ):
        fail(f"wrong update request target: {patch}")

    expected_body = {
        "backupLocations": [
            {
                "server": "backup01.lab.example",
                "port": 22,
                "protocol": "SFTP",
                "username": "svc-vcf-\"backup\"",
                "directoryPath": "/exports/vcf\\nightly",
            }
        ]
    }
    try:
        body = json.loads(patch["body"])
    except json.JSONDecodeError as error:
        fail(f"PATCH body is not JSON: {error}")
    if body != expected_body:
        fail(f"PATCH JSON shape differs from the contract scenario: {body!r}")
    location = body["backupLocations"][0]
    forbidden = {"password", "sshFingerprint"}
    if forbidden.intersection(location):
        fail("unset BackupLocation optionals must be omitted")
    if {"encryption", "backupSchedules"}.intersection(body):
        fail("unset BackupConfigurationSpec optionals must be omitted")

    expected_task_path = "/v1/tasks/" + quote(server_info["task_id"], safe="")
    for index, entry in enumerate((first_poll, second_poll), start=1):
        if (
            entry["operationId"],
            entry["method"],
            entry["target"],
            entry["path"],
            entry["query"],
        ) != (
            "getTask",
            "GET",
            expected_task_path,
            expected_task_path,
            "",
        ):
            fail(f"wrong task poll {index}: {entry}")
        if entry["body"] != "":
            fail(f"GET poll {index} unexpectedly sent a body")
        if "content-type" in entry["headers"]:
            fail(f"GET poll {index} must omit Content-Type")

    for entry in entries:
        headers = entry["headers"]
        if headers.get("accept") != "application/json":
            fail("every request must send Accept: application/json")
        if headers.get("authorization") != "Bearer " + server_info["access_token"]:
            fail("every request must send the supplied bearer token")
        if "transfer-encoding" in headers:
            fail("requests must not use transfer encoding")
    if patch["headers"].get("content-type") != "application/json":
        fail("PATCH must send Content-Type: application/json")
    if patch["headers"].get("content-length") != str(
        len(patch["body"].encode("utf-8"))
    ):
        fail("PATCH Content-Length must match the UTF-8 request body")


def main() -> None:
    check_protected_files()
    check_contract_metadata()

    with tempfile.TemporaryDirectory(prefix="vcf91-0037-") as temporary:
        temp = Path(temporary)
        classes = temp / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            [
                "javac",
                "--release",
                "17",
                "-d",
                str(classes),
                str(PROJECT / "VcfBackupClient.java"),
                str(PROJECT / "tests/TestMain.java"),
            ],
            text=True,
            capture_output=True,
            timeout=10,
        )
        if compile_result.returncode != 0:
            sys.stderr.write(compile_result.stdout + compile_result.stderr)
            fail("Java sources did not compile")

        request_log = temp / "requests.jsonl"
        port_file = temp / "port"
        mock = subprocess.Popen(
            [
                sys.executable,
                str(PROJECT / "tests/mock_server.py"),
                "--contract",
                str(PROJECT / "docs/contract.json"),
                "--log",
                str(request_log),
                "--port-file",
                str(port_file),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            server_info = wait_for_server(port_file, mock)
            run_result = subprocess.run(
                [
                    "java",
                    "-cp",
                    str(classes),
                    "TestMain",
                    f"http://127.0.0.1:{server_info['port']}/",
                    server_info["access_token"],
                    server_info["task_id"],
                ],
                text=True,
                capture_output=True,
                timeout=12,
            )
            if run_result.returncode != 0:
                sys.stderr.write(run_result.stdout + run_result.stderr)
                fail("TestMain failed")
            if run_result.stdout.strip() != "SUCCESSFUL":
                fail(f"unexpected TestMain output: {run_result.stdout!r}")
        finally:
            mock.terminate()
            try:
                mock.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.communicate(timeout=3)

        check_wire_log(request_log, server_info)

    print("PASS: spec-derived async backup client")


if __name__ == "__main__":
    main()
