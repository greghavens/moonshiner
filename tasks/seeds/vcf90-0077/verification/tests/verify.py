#!/usr/bin/env python3
"""Deterministic integration verifier for the single-file Java client."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_IDS = [
    "33333333-3333-4333-8333-333333333333",
    "11111111-1111-4111-8111-111111111111",
    "55555555-5555-4555-8555-555555555555",
    "22222222-2222-4222-8222-222222222222",
    "44444444-4444-4444-8444-444444444444",
]


def fail(message: str) -> None:
    raise AssertionError(message)


def assert_source_metadata() -> None:
    source = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))
    expected = {
        "repository": "https://github.com/vmware/vcf-api-specs",
        "license": "Apache-2.0",
        "tag": "9.0.0.0",
        "commit_sha": "85151f6b1bb58f13b6ac0304bfec53904bea085f",
        "spec_path": "specifications/vcf-operations/vcf-operations-openapi.json",
        "operation_ids": ["acquireToken", "getResources"],
    }
    if source != expected:
        fail("docs/official_sources.json no longer identifies the pinned 9.0 specification")

    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    if contract["source"]["tag"] != "9.0.0.0" or "9.1" in json.dumps(contract):
        fail("contract is not pinned exclusively to VCF 9.0.0.0")
    operations = contract["operations"]
    if set(operations) != {"acquireToken", "getResources"}:
        fail("contract operationIds changed")
    if (operations["acquireToken"]["method"], operations["acquireToken"]["path"]) != (
            "POST", "/api/auth/token/acquire"):
        fail("acquireToken wire contract changed")
    if (operations["getResources"]["method"], operations["getResources"]["path"]) != (
            "GET", "/api/resources"):
        fail("getResources wire contract changed")
    if contract["base_path"] != "/suite-api":
        fail("VCF Operations base path changed")
    if contract["security_scheme"] != {
            "name": "Token-based-authorization",
            "type": "apiKey",
            "in": "header",
            "header": "Authorization",
    }:
        fail("token header contract changed")


def wait_for_port(port_file: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            fail(f"mock exited before startup: {stdout}{stderr}")
        if port_file.exists() and port_file.read_text(encoding="ascii").strip():
            return int(port_file.read_text(encoding="ascii"))
        time.sleep(0.02)
    fail("mock did not publish its loopback port")


def expected_query(page: int) -> dict[str, list[str]]:
    return {
        "adapterKind": ["VMWARE"],
        "resourceKind": ["VirtualMachine"],
        "page": [str(page)],
        "pageSize": ["2"],
    }


def assert_wire(log_path: Path) -> None:
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    expected_sequence = [
        ("acquireToken", "POST", "/suite-api/api/auth/token/acquire"),
        ("getResources", "GET", "/suite-api/api/resources"),
        ("getResources", "GET", "/suite-api/api/resources"),
        ("acquireToken", "POST", "/suite-api/api/auth/token/acquire"),
        ("getResources", "GET", "/suite-api/api/resources"),
        ("getResources", "GET", "/suite-api/api/resources"),
    ]
    actual_sequence = [(r["operationId"], r["method"], r["path"]) for r in records]
    if actual_sequence != expected_sequence:
        fail(f"request sequence mismatch: {actual_sequence!r}")

    for index in (0, 3):
        request = records[index]
        if request["query"] != {}:
            fail("acquireToken must not send a query string")
        if request["headers"] != {
                "accept": "application/json",
                "content-type": "application/json",
        }:
            fail(f"acquireToken headers have the wrong wire shape: {request['headers']!r}")
        credentials = json.loads(request["body"])
        if credentials != {"username": "ops-user", "password": 'p@ss"word'}:
            fail(f"credential JSON has the wrong fields: {credentials!r}")
        if set(credentials) != {"username", "password"}:
            fail("unset optional authSource must be omitted, not sent empty")

    resource_expectations = [
        (1, 0, "token-1"),
        (2, 1, "token-1"),
        (4, 1, "token-2"),
        (5, 2, "token-2"),
    ]
    optional_names = {
        "name", "regex", "collectorName", "collectorId", "maintenanceScheduleId",
        "adapterInstanceId", "recentlyAdded", "resourceState", "resourceStatus",
        "resourceHealth", "parentId", "credentialId", "resourceId", "propertyName",
        "propertyValue", "statKey", "statKeyLowerBound", "statKeyUpperBound",
        "statKeyInclusive", "includeRelated",
    }
    for index, page, token in resource_expectations:
        request = records[index]
        if request["query"] != expected_query(page):
            fail(f"getResources query has the wrong wire shape: {request['query']!r}")
        if optional_names.intersection(request["query"]):
            fail("unset optional getResources fields must be omitted")
        if any(values == [""] for values in request["query"].values()):
            fail("query parameters must not be sent empty")
        if request["headers"] != {
                "accept": "application/json",
                "authorization": token,
        }:
            fail(f"getResources headers have the wrong wire shape: {request['headers']!r}")
        if request["body"] != "":
            fail("getResources must not send a request body")


def run() -> None:
    assert_source_metadata()
    with tempfile.TemporaryDirectory(prefix="vcf90-0077-") as temp_name:
        temp = Path(temp_name)
        classes = temp / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            ["javac", "-encoding", "UTF-8", "-d", str(classes),
             str(ROOT / "VcfOperationsClient.java"), str(ROOT / "tests" / "TestMain.java")],
            cwd=ROOT, text=True, capture_output=True, timeout=10)
        if compile_result.returncode != 0:
            fail("javac failed:\n" + compile_result.stdout + compile_result.stderr)

        port_file = temp / "port"
        log_file = temp / "requests.jsonl"
        mock = subprocess.Popen(
            [sys.executable, str(ROOT / "tests" / "mock_server.py"),
             "--port-file", str(port_file), "--log-file", str(log_file)],
            cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            port = wait_for_port(port_file, mock)
            result = subprocess.run(
                ["java", "-cp", str(classes), "TestMain", f"http://127.0.0.1:{port}"],
                cwd=ROOT, text=True, capture_output=True, timeout=10)
            if result.returncode != 0:
                fail("TestMain failed:\n" + result.stdout + result.stderr)
            if result.stdout.strip() != ",".join(EXPECTED_IDS):
                fail(f"unexpected TestMain output: {result.stdout!r}")
            assert_wire(log_file)
        finally:
            mock.terminate()
            try:
                mock.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.communicate()


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("PASS: VCF Operations token refresh and request wire contract")
