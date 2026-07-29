#!/usr/bin/env python3
"""Deterministic protected verifier for VcfDepotClient."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
PROTECTED_HASHES = {
    "docs/contract.json": "765abb8aae17a213d550c5dcc2e076fddf2aaf026df25e457bc1a6c6cff211aa",
    "docs/official_sources.json": "d598f12c2d451c15487caecf10bd0742c89480bf327de352ded4e127ff8b4a8e",
    "tests/TestMain.java": "9cccb84d768172e0d8c3634ce027585ba1b39c143ea8c170eb2269656caba635",
    "tests/mock_server.py": "d0e44c45ac0c4e5091d9f50fd0184f47de32daacaa1852e6135300113e9259e6",
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
        ("updateDepotSettings", "PUT", "/v1/system/settings/depot"),
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
    operation = contract["operations"][0]
    if (
        operation["requestBody"] != {
            "required": True,
            "mediaType": "application/json",
            "schema": "DepotSettings",
        }
        or set(operation["responses"]) != {"202", "400", "500"}
    ):
        fail("focused operation contract changed")
    depot = contract["schemas"]["DepotSettings"]
    account = contract["schemas"]["DepotAccount"]
    if set(depot["properties"]) != {
        "vmwareAccount",
        "offlineAccount",
        "depotConfiguration",
    }:
        fail("DepotSettings projection changed")
    if set(account["properties"]) != {
        "username",
        "password",
        "status",
        "message",
        "downloadToken",
        "downloadActivationCode",
    }:
        fail("DepotAccount projection changed")


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
    if len(entries) != 2:
        fail(f"expected the initial PUT and one retry, got {len(entries)} requests")
    if [entry["sequence"] for entry in entries] != [1, 2]:
        fail("request sequence is not deterministic")
    if [entry["responseStatus"] for entry in entries] != [500, 202]:
        fail("mock did not exercise the committed transient-failure scenario")
    if [entry["effectCountAfter"] for entry in entries] != [1, 1]:
        fail("the retry duplicated the depot mutation effect")

    expected_body = json.dumps(
        {
            "vmwareAccount": {
                "username": server_info["username"],
                "password": server_info["password"],
            }
        },
        separators=(",", ":"),
    )
    for index, entry in enumerate(entries, start=1):
        if (
            entry["operationId"],
            entry["method"],
            entry["target"],
            entry["path"],
            entry["query"],
        ) != (
            "updateDepotSettings",
            "PUT",
            "/v1/system/settings/depot",
            "/v1/system/settings/depot",
            "",
        ):
            fail(f"wrong updateDepotSettings request {index}: {entry}")
        if entry["body"] != expected_body:
            fail(f"PUT {index} has the wrong compact UTF-8 body: {entry['body']!r}")
        headers = entry["headers"]
        if headers.get("accept") != "application/json":
            fail(f"PUT {index} must send Accept: application/json")
        if headers.get("authorization") != "Bearer " + server_info["access_token"]:
            fail(f"PUT {index} must send the supplied bearer token")
        if headers.get("content-type") != "application/json":
            fail(f"PUT {index} must send Content-Type: application/json")
        if headers.get("content-length") != str(len(expected_body.encode("utf-8"))):
            fail(f"PUT {index} Content-Length does not match its UTF-8 body")
        if "transfer-encoding" in headers:
            fail(f"PUT {index} must not use transfer encoding")

        payload = json.loads(entry["body"])
        if set(payload) != {"vmwareAccount"}:
            fail("offlineAccount and depotConfiguration must be omitted")
        if set(payload["vmwareAccount"]) != {"username", "password"}:
            fail(
                "unset status, message, downloadToken, and "
                "downloadActivationCode must be omitted"
            )
    if entries[0]["body"].encode("utf-8") != entries[1]["body"].encode("utf-8"):
        fail("the retry must reuse the exact serialized request bytes")


def main() -> None:
    check_protected_files()
    check_contract_metadata()

    with tempfile.TemporaryDirectory(prefix="vcf91-0040-") as temporary:
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
                str(PROJECT / "VcfDepotClient.java"),
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
                    server_info["username"],
                    server_info["password"],
                ],
                text=True,
                capture_output=True,
                timeout=12,
            )
            if run_result.returncode != 0:
                sys.stderr.write(run_result.stdout + run_result.stderr)
                fail("TestMain failed")
            if run_result.stdout.strip() != "UPDATED":
                fail(f"unexpected TestMain output: {run_result.stdout!r}")
        finally:
            mock.terminate()
            try:
                mock.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.communicate(timeout=3)

        check_wire_log(request_log, server_info)

    print("PASS: spec-derived retry-safe depot client")


if __name__ == "__main__":
    main()
