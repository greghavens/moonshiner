#!/usr/bin/env python3
"""Deterministic acceptance verifier for the NSX Policy Java client."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NoReturn


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
EXPECTED_SPEC = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
EXPECTED_OPERATIONS = {
    "ListAlarms": ("GET", "/infra/realized-state/alarms"),
    "ListTraceflowObservations": (
        "GET",
        "/infra/traceflows/{traceflow-id}/observations",
    ),
}
EXPECTED_QUERY_PARAMETERS = {
    "ListAlarms": [
        "cursor",
        "included_fields",
        "page_size",
        "sort_ascending",
        "sort_by",
    ],
    "ListTraceflowObservations": ["enforcement_point_path"],
}
EXPECTED_RESPONSE_SCHEMAS = {
    "ListAlarms": "PolicyAlarmResourceListResult",
    "ListTraceflowObservations": "TraceflowObservationListResult",
}


def fail(message: str) -> NoReturn:
    raise AssertionError(message)


def load_json(path: Path) -> object:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def verify_provenance_and_contract() -> None:
    contract = load_json(ROOT / "docs" / "contract.json")
    sources = load_json(ROOT / "docs" / "official_sources.json")
    if not isinstance(contract, dict) or not isinstance(sources, dict):
        fail("contract and source records must be JSON objects")

    derived = contract.get("derived_from")
    if not isinstance(derived, dict):
        fail("contract is missing derived_from")
    if derived.get("repository") != "vmware/vcf-api-specs":
        fail("contract repository changed")
    if derived.get("commit") != EXPECTED_COMMIT:
        fail("contract commit changed")
    if derived.get("spec_path") != EXPECTED_SPEC:
        fail("contract spec path changed")
    if derived.get("license") != "Apache-2.0":
        fail("contract license changed")
    if contract.get("basePath") != "/policy/api/v1":
        fail("contract base path changed")

    if sources.get("repository_commit_sha") != EXPECTED_COMMIT:
        fail("official source commit changed")
    if sources.get("spec_path") != EXPECTED_SPEC:
        fail("official source spec path changed")
    if sources.get("license") != "Apache-2.0":
        fail("official source license changed")

    operations = contract.get("operations")
    source_operations = sources.get("operations")
    if not isinstance(operations, list) or not isinstance(source_operations, list):
        fail("operation source records must be arrays")
    by_id = {
        item.get("operationId"): item
        for item in operations
        if isinstance(item, dict)
    }
    source_by_id = {
        item.get("operationId"): item
        for item in source_operations
        if isinstance(item, dict)
    }
    if set(by_id) != set(EXPECTED_OPERATIONS):
        fail("contract must name exactly the two selected operations")
    if set(source_by_id) != set(EXPECTED_OPERATIONS):
        fail("official_sources must record every selected operationId")

    for operation_id, (method, path) in EXPECTED_OPERATIONS.items():
        operation = by_id[operation_id]
        if (operation.get("method"), operation.get("path")) != (method, path):
            fail(f"{operation_id} wire contract changed")
        success = operation.get("success_response")
        if (
            not isinstance(success, dict)
            or success.get("status") != 200
            or success.get("media_type") != "application/json"
            or success.get("schema") != EXPECTED_RESPONSE_SCHEMAS[operation_id]
        ):
            fail(f"{operation_id} success response changed")
        query_parameters = operation.get("query_parameters")
        if not isinstance(query_parameters, list) or any(
            not isinstance(parameter, dict)
            or parameter.get("required") is not False
            for parameter in query_parameters
        ):
            fail(f"{operation_id} optional query contract changed")
        if [
            parameter.get("name") for parameter in query_parameters
        ] != EXPECTED_QUERY_PARAMETERS[operation_id]:
            fail(f"{operation_id} query parameters changed")
        path_parameters = operation.get("path_parameters", [])
        if operation_id == "ListTraceflowObservations":
            if path_parameters != [
                {"name": "traceflow-id", "type": "string", "required": True}
            ]:
                fail("ListTraceflowObservations path parameter changed")
        elif path_parameters:
            fail("ListAlarms must not have path parameters")

        source = source_by_id[operation_id]
        if (source.get("method"), source.get("path")) != (method, path):
            fail(f"{operation_id} official source route changed")
        source_url = source.get("source")
        if (
            not isinstance(source_url, str)
            or EXPECTED_COMMIT not in source_url
            or EXPECTED_SPEC not in source_url
            or not source_url.startswith("https://github.com/vmware/vcf-api-specs/blob/")
        ):
            fail(f"{operation_id} source must be the pinned specification")


def wait_for_port(port_file: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            fail(f"mock exited before ready\nstdout={stdout}\nstderr={stderr}")
        if port_file.exists():
            value = port_file.read_text(encoding="ascii").strip()
            if value:
                return int(value)
        time.sleep(0.02)
    fail("timed out waiting for loopback mock")


def run_client() -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    if shutil.which("javac") is None or shutil.which("java") is None:
        fail("JDK tools javac and java are required")

    with tempfile.TemporaryDirectory(prefix="vcf91-0086-") as temp_name:
        temp = Path(temp_name)
        classes = temp / "classes"
        classes.mkdir()
        compile_result = subprocess.run(
            [
                "javac",
                "--release",
                "17",
                "-encoding",
                "UTF-8",
                "-d",
                os.fspath(classes),
                os.fspath(ROOT / "NsxPolicyClient.java"),
                os.fspath(ROOT / "test" / "TestMain.java"),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )
        if compile_result.returncode != 0:
            fail(
                "Java compilation failed\n"
                + compile_result.stdout
                + compile_result.stderr
            )

        port_file = temp / "port"
        request_log = temp / "requests.jsonl"
        mock = subprocess.Popen(
            [
                sys.executable,
                os.fspath(ROOT / "test" / "mock_nsx.py"),
                "--contract",
                os.fspath(ROOT / "docs" / "contract.json"),
                "--alarms",
                os.fspath(ROOT / "test" / "fixtures" / "alarms.json"),
                "--observations",
                os.fspath(ROOT / "test" / "fixtures" / "observations.json"),
                "--request-log",
                os.fspath(request_log),
                "--port-file",
                os.fspath(port_file),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            port = wait_for_port(port_file, mock)
            result = subprocess.run(
                [
                    "java",
                    "-cp",
                    os.fspath(classes),
                    "TestMain",
                    f"http://127.0.0.1:{port}",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
                check=False,
            )
        finally:
            mock.terminate()
            try:
                mock_stdout, mock_stderr = mock.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock_stdout, mock_stderr = mock.communicate(timeout=3)

        entries: list[dict[str, object]] = []
        if request_log.exists():
            for line in request_log.read_text(encoding="utf-8").splitlines():
                value = json.loads(line)
                if not isinstance(value, dict):
                    fail("request log entries must be JSON objects")
                entries.append(value)

        if mock.returncode not in (-15, 0):
            fail(
                f"mock exited unexpectedly with {mock.returncode}\n"
                f"stdout={mock_stdout}\nstderr={mock_stderr}"
            )
        return result, entries


def verify_wire(entries: list[dict[str, object]]) -> None:
    expected_targets = [
        "/policy/api/v1/infra/realized-state/alarms",
        "/policy/api/v1/infra/traceflows/tf%20incident%2F42/observations",
    ]
    if len(entries) != len(expected_targets):
        fail(
            f"expected exactly two NSX Policy requests, observed {len(entries)}: "
            f"{[entry.get('target') for entry in entries]}"
        )

    expected_auth = "Basic " + base64.b64encode(
        b"audit-reader:s3cret"
    ).decode("ascii")
    for index, (entry, target) in enumerate(zip(entries, expected_targets, strict=True)):
        if entry.get("method") != "GET":
            fail(f"request {index + 1} must use GET")
        if entry.get("target") != target:
            fail(
                f"request {index + 1} target differs from the specification: "
                f"{entry.get('target')!r}"
            )
        if entry.get("body") != "":
            fail(f"request {index + 1} must not have a body")
        headers = entry.get("headers")
        if not isinstance(headers, dict):
            fail(f"request {index + 1} headers were not logged")
        if headers.get("accept") != "application/json":
            fail(f"request {index + 1} must accept application/json")
        if headers.get("authorization") != expected_auth:
            fail(f"request {index + 1} Basic authorization is incorrect")
        if "?" in target:
            fail("unset optional query parameters must be omitted")


def main() -> None:
    verify_provenance_and_contract()
    result, entries = run_client()
    verify_wire(entries)
    if result.returncode != 0:
        fail(
            f"TestMain failed with exit {result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    if result.stdout.strip() != "TEST_MAIN_OK":
        fail(f"unexpected TestMain output: {result.stdout!r}")
    print("PASS: contract provenance, exact wire shape, and diagnostic evidence")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
