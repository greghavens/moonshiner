#!/usr/bin/env python3
"""Deterministic protected verifier for VcfDomainInventoryClient."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs


PROJECT = Path(__file__).resolve().parents[1]
COMMIT_SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
PROTECTED_HASHES = {
    "docs/contract.json": "3d9fde11a8d735f418021814a04147c8cde9fbd33ccd50356486c7ea8cf373ff",
    "docs/official_sources.json": "f1604d5fbf475fb6b6cab78bf42350969763ad9fecbae4faa0aa0527b79fd5d0",
    "tests/TestMain.java": "ba41691c08dd12f1046733489eb44aa7e37d2f1d6a7c6971ad93129d184e4bfb",
    "tests/mock_server.py": "5d3d6b4d5221c29b74e15ed13762e5cf15f5d26412b23775befc0fc9d20f7eae",
}
OPTIONAL_FILTERS = {
    "type",
    "name",
    "vcFqdn",
    "vcInstanceId",
    "isManagementSsoDomain",
    "useCache",
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
    sources = json.loads(
        (PROJECT / "docs/official_sources.json").read_text(encoding="utf-8")
    )
    if (
        contract["derived_from"]["repository_commit_sha"] != COMMIT_SHA
        or sources["repository"]["commit_sha"] != COMMIT_SHA
    ):
        fail("official source commit is not pinned")
    if (
        contract["derived_from"]["spec_path"] != SPEC_PATH
        or sources["specification"]["path"] != SPEC_PATH
    ):
        fail("official specification path changed")

    operations = contract["operations"]
    if len(operations) != 1:
        fail("contract must contain only getDomains")
    operation = operations[0]
    expected_operation = ("getDomains", "GET", "/v1/domains")
    if (
        operation["operationId"],
        operation["method"],
        operation["path"],
    ) != expected_operation:
        fail("focused getDomains contract changed")
    parameters = {
        item["name"]: (
            item["in"],
            item.get("required"),
            item["schema"]["type"],
            item["schema"].get("format"),
        )
        for item in operation["parameters"]
    }
    if set(parameters) != OPTIONAL_FILTERS | {"pageNumber", "pageSize"}:
        fail("getDomains parameter projection changed")
    if parameters["pageNumber"] != ("query", False, "integer", "int32"):
        fail("pageNumber contract changed")
    if parameters["pageSize"] != ("query", False, "integer", "int32"):
        fail("pageSize contract changed")

    recorded = sources["operations"]
    if len(recorded) != 1:
        fail("official_sources must name every and only the selected operation")
    record = recorded[0]
    if (
        record["operationId"],
        record["method"],
        record["path"],
        record["repository_commit_sha"],
        record["spec_path"],
    ) != (*expected_operation, COMMIT_SHA, SPEC_PATH):
        fail("operation provenance changed")
    for projection in sources["schema_projections"]:
        if (
            projection["repository_commit_sha"] != COMMIT_SHA
            or projection["spec_path"] != SPEC_PATH
        ):
            fail("schema provenance is not pinned at commit granularity")


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
    expected_pages = [0, 1, 2, 0, 1, 2]
    if len(entries) != len(expected_pages):
        fail(f"expected two complete traversals, got {len(entries)} requests")
    if [entry["sequence"] for entry in entries] != list(range(1, 7)):
        fail("request log sequence changed")

    for entry, page_number in zip(entries, expected_pages, strict=True):
        expected_query = f"pageNumber={page_number}&pageSize=2"
        expected_target = "/v1/domains?" + expected_query
        if (
            entry["operationId"],
            entry["method"],
            entry["target"],
            entry["path"],
            entry["query"],
        ) != (
            "getDomains",
            "GET",
            expected_target,
            "/v1/domains",
            expected_query,
        ):
            fail(f"wrong getDomains wire target: {entry}")

        parsed = parse_qs(entry["query"], keep_blank_values=True, strict_parsing=True)
        if set(parsed) != {"pageNumber", "pageSize"}:
            fail(f"unset optional fields were not omitted: {entry['query']!r}")
        if OPTIONAL_FILTERS.intersection(parsed):
            fail(f"unset optional filter was transmitted: {entry['query']!r}")
        if any(value == "" for values in parsed.values() for value in values):
            fail(f"empty query value was transmitted: {entry['query']!r}")

        headers = entry["headers"]
        if headers.get("accept") != "application/json":
            fail("getDomains must send Accept: application/json")
        if headers.get("authorization") != "Bearer " + server_info["access_token"]:
            fail("getDomains used the wrong bearer token")
        if "content-type" in headers:
            fail("GET must omit Content-Type")
        if "transfer-encoding" in headers:
            fail("GET must omit transfer encoding")
        if entry["body"] != "":
            fail("GET must have an empty request body")


def main() -> None:
    check_protected_files()
    check_contract_metadata()

    with tempfile.TemporaryDirectory(prefix="vcf91-0039-") as temporary:
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
                str(PROJECT / "VcfDomainInventoryClient.java"),
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
            domain_args = [
                field
                for domain in server_info["domains"]
                for field in (
                    domain["id"],
                    domain["name"],
                    domain["status"],
                    domain["type"],
                )
            ]
            run_result = subprocess.run(
                [
                    "java",
                    "-cp",
                    str(classes),
                    "TestMain",
                    f"http://127.0.0.1:{server_info['port']}/",
                    server_info["access_token"],
                    *domain_args,
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

    print("PASS: spec-derived complete stable domain inventory")


if __name__ == "__main__":
    main()
