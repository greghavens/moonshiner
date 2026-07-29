#!/usr/bin/env python3
"""Deterministic protected verifier for VcfDomainClient."""

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
    "docs/contract.json": "ff59ae61f086d03b1dc4ba49c51d54c035c49a50f0a3cb59d1b79b2ea008fcf4",
    "docs/official_sources.json": "b468dddd175f22e98fa06823a4f05233858da1aab7808f88a0b16ed6b4a4b0d7",
    "tests/TestMain.java": "be49d63f06e5daa667a0a7861d954b6a20d3e03d02db704ac45ee2a336a3f567",
    "tests/mock_server.py": "7d3fcf8bc8f8fd61878ec7835ae897a2243dbd6468f02de04d5917c315ae9ed0",
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
        ("createToken", "POST", "/v1/tokens"),
        ("getDomain", "GET", "/v1/domains/{id}"),
        ("refreshAccessToken", "PATCH", "/v1/tokens/access-token/refresh"),
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
    for operation in source["operations"]:
        if (
            operation["repository_commit_sha"] != sha
            or operation["spec_path"] != spec_path
        ):
            fail("operation provenance is not recorded at commit granularity")


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


def require_target(entry: dict, operation_id: str, method: str, target: str) -> None:
    if (
        entry["operationId"],
        entry["method"],
        entry["target"],
        entry["path"],
        entry["query"],
    ) != (operation_id, method, target, target, ""):
        fail(f"wrong {operation_id} request target: {entry}")


def check_wire_log(log_path: Path, server_info: dict) -> None:
    entries = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(entries) != 6:
        fail(f"expected six requests for the refresh/resume flow, got {len(entries)}")
    if [entry["sequence"] for entry in entries] != list(range(1, 7)):
        fail("request sequence is not deterministic")

    create, first, expired, refresh, retry, final = entries

    require_target(create, "createToken", "POST", "/v1/tokens")
    try:
        create_body = json.loads(create["body"])
    except json.JSONDecodeError as error:
        fail(f"createToken body is not JSON: {error}")
    expected_create = {
        "username": server_info["username"],
        "password": server_info["password"],
    }
    if create_body != expected_create:
        fail(f"createToken JSON shape differs from TokenCreationSpec: {create_body!r}")
    if {"apiKey", "idToken"}.intersection(create_body):
        fail("unset TokenCreationSpec optionals must be omitted")

    expected_paths = [
        "/v1/domains/" + quote(domain_id, safe="")
        for domain_id in server_info["domain_ids"]
    ]
    get_entries = [first, expired, retry, final]
    expected_get_paths = [
        expected_paths[0],
        expected_paths[1],
        expected_paths[1],
        expected_paths[2],
    ]
    expected_tokens = [
        server_info["initial_token"],
        server_info["initial_token"],
        server_info["refreshed_token"],
        server_info["refreshed_token"],
    ]
    for index, (entry, path, token) in enumerate(
        zip(get_entries, expected_get_paths, expected_tokens, strict=True),
        start=1,
    ):
        require_target(entry, "getDomain", "GET", path)
        if entry["body"] != "":
            fail(f"domain GET {index} unexpectedly sent a body")
        if "content-type" in entry["headers"]:
            fail(f"domain GET {index} must omit Content-Type")
        if entry["headers"].get("authorization") != "Bearer " + token:
            fail(f"domain GET {index} used the wrong access token")

    require_target(
        refresh,
        "refreshAccessToken",
        "PATCH",
        "/v1/tokens/access-token/refresh",
    )
    try:
        refresh_body = json.loads(refresh["body"])
    except json.JSONDecodeError as error:
        fail(f"refreshAccessToken body is not JSON: {error}")
    if not isinstance(refresh_body, str) or refresh_body != server_info["refresh_token"]:
        fail("refreshAccessToken body must be exactly the refresh-token JSON string")

    for entry in entries:
        headers = entry["headers"]
        if headers.get("accept") != "application/json":
            fail("every request must send Accept: application/json")
        if "transfer-encoding" in headers:
            fail("requests must not use transfer encoding")
    for entry in (create, refresh):
        if entry["headers"].get("content-type") != "application/json":
            fail(f"{entry['operationId']} must send Content-Type: application/json")
        if entry["headers"].get("content-length") != str(
            len(entry["body"].encode("utf-8"))
        ):
            fail(f"{entry['operationId']} Content-Length does not match its UTF-8 body")
        if "authorization" in entry["headers"]:
            fail(f"{entry['operationId']} must omit Authorization")


def main() -> None:
    check_protected_files()
    check_contract_metadata()

    with tempfile.TemporaryDirectory(prefix="vcf91-0038-") as temporary:
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
                str(PROJECT / "VcfDomainClient.java"),
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
                    server_info["username"],
                    server_info["password"],
                    *server_info["domain_ids"],
                    *server_info["domain_names"],
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

    print("PASS: spec-derived domain token refresh client")


if __name__ == "__main__":
    main()
