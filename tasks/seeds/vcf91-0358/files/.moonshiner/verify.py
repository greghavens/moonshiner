#!/usr/bin/env python3
"""Deterministic protected verifier for VcfAutomationClient."""

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
    "docs/contract.json": "96231c566e250f25ec28f49bbd04d2b61b2ffebfd1d377eeaa7bb2bf8e1e9831",
    "docs/official_sources.json": "c809153d259b94cf5a6bd9a6a21d590bc879fe2731b47e726b31fd895c3f309a",
    "tests/TestMain.java": "f7463b6bfe4d2a8961396b15392f292738f14c2bf0a7fced6833a3cb8989d782",
    "tests/mock_automation.py": "ef71f304b9c09723c1fdfe2d0819423fda0d7586eb923900095e5ea479f55b44",
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
    if contract["source_authority"]["kind"] != "official-reference-documentation":
        fail("contract is not labeled reference-derived")
    statement = contract["source_authority"]["statement"]
    if (
        contract["source_authority"]["published_specification"] is not False
        or "rather than a published specification" not in statement
        or "vmware/vcf-api-specs" not in statement
    ):
        fail("contract must plainly distinguish reference documentation from a spec")

    expected_operations = {
        (
            "deleteDeployment",
            "Delete Deployment",
            "DELETE",
            "/deployment/api/deployments/{deploymentId}",
        ),
        (
            "getRequest",
            "Get Request",
            "GET",
            "/deployment/api/requests/{requestId}",
        ),
    }
    projected = {
        (
            item["operation_key"],
            item["reference_title"],
            item["method"],
            item["path"],
        )
        for item in contract["operations"]
    }
    if projected != expected_operations or len(contract["operations"]) != 2:
        fail("focused operation projection changed")
    if any("operationId" in item for item in contract["operations"]):
        fail("reference-derived contract must not invent vendor operationIds")

    expected_pages = {
        (
            "https://developer.broadcom.com/xapis/vm-apps-org-deployment/9.1/deployment/api/deployments/deploymentId/delete/",
            "2026-08-16",
            "deleteDeployment",
            "Delete Deployment",
            "DELETE",
            "/deployment/api/deployments/{deploymentId}",
        ),
        (
            "https://developer.broadcom.com/xapis/vm-apps-org-deployment/9.1/deployment/api/requests/requestId/get/",
            "2026-08-16",
            "getRequest",
            "Get Request",
            "GET",
            "/deployment/api/requests/{requestId}",
        ),
    }
    recorded_pages = {
        (
            page["url"],
            page["fetched_on"],
            page["operation"]["operation_key"],
            page["operation"]["reference_title"],
            page["operation"]["method"],
            page["operation"]["path"],
        )
        for page in sources["pages"]
    }
    if recorded_pages != expected_pages or len(sources["pages"]) != 2:
        fail("official page provenance changed")
    if (
        sources["source_kind"] != "authoritative-reference-documentation"
        or sources["specification_available"] is not False
        or sources["vcf_api_specs_repository"]["license"] != "Apache-2.0"
    ):
        fail("reference source basis changed")

    statuses = contract["schemas"]["Request"]["properties"]["status"]["values"]
    expected_statuses = [
        "CREATED",
        "PENDING",
        "INITIALIZATION",
        "CHECKING_APPROVAL",
        "APPROVAL_PENDING",
        "USER_INTERACTION_PENDING",
        "INPROGRESS",
        "COMPLETION",
        "APPROVAL_REJECTED",
        "ABORTED",
        "SUCCESSFUL",
        "FAILED",
    ]
    if statuses != expected_statuses:
        fail("Request status projection changed")


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


def one_header(entry: dict, name: str) -> str | None:
    values = entry["headers"].get(name)
    if values is None:
        return None
    if len(values) != 1:
        fail(f"{entry['operation_key']} sent {len(values)} {name} headers")
    return values[0]


def check_wire_log(log_path: Path, server_info: dict, mode: str) -> None:
    entries = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    expected = {
        "retry": (
            ["deleteDeployment", "deleteDeployment"] + ["getRequest"] * 4,
            [503, 200, 200, 200, 200, 200],
        ),
        "all-statuses": (
            ["deleteDeployment"] + ["getRequest"] * 9,
            [200] * 10,
        ),
        "success-no-completed-at": (
            ["deleteDeployment", "getRequest"],
            [200, 200],
        ),
        "sleep-check": (
            ["deleteDeployment", "getRequest"],
            [200, 200],
        ),
        "terminal-failures": (
            ["deleteDeployment", "getRequest"] * 3,
            [200] * 6,
        ),
        "invalid-delete": (
            ["deleteDeployment"] * 18,
            [200] * 18,
        ),
        "invalid-get": (
            ["deleteDeployment", "getRequest"] * 20,
            [200] * 40,
        ),
        "non-200": (
            ["deleteDeployment", "deleteDeployment", "getRequest"],
            [201, 200, 202],
        ),
    }
    expected_operations, expected_statuses = expected[mode]
    if len(entries) != len(expected_operations):
        fail(
            f"{mode}: expected {len(expected_operations)} requests, got {len(entries)}"
        )
    if [entry["sequence"] for entry in entries] != list(range(1, len(entries) + 1)):
        fail("request log sequence is not deterministic")
    if [entry["operation_key"] for entry in entries] != expected_operations:
        fail(f"{mode}: operation order differs from the expected workflow")
    if [entry["response_status"] for entry in entries] != expected_statuses:
        fail(f"{mode}: mock response sequence changed")

    delete_path = "/deployment/api/deployments/" + quote(
        server_info["deployment_id"], safe=""
    )
    request_path = "/deployment/api/requests/" + quote(
        server_info["request_id"], safe=""
    )
    for index, entry in enumerate(entries, start=1):
        if entry["operation_key"] == "deleteDeployment":
            expected_wire = ("DELETE", delete_path, delete_path, "", "")
        else:
            expected_wire = ("GET", request_path, request_path, "", "")
        actual_wire = (
            entry["method"],
            entry["target"],
            entry["path"],
            entry["query"],
            entry["body"],
        )
        if actual_wire != expected_wire:
            fail(f"{mode}: wrong request {index}: {entry}")
        if one_header(entry, "accept") != "application/json":
            fail("every request must send exactly Accept: application/json")
        if one_header(entry, "authorization") != "Bearer " + server_info["access_token"]:
            fail("every request must send exactly the supplied bearer token")
        if one_header(entry, "content-type") is not None:
            fail("bodyless DELETE and GET operations must omit Content-Type")
        if "transfer-encoding" in entry["headers"]:
            fail("bodyless operations must not use transfer encoding")

    if [entry["mutation_count_after"] for entry in entries] != [1] * len(entries):
        fail("retry duplicated the delete effect")

    if mode == "retry":
        actual_states = [
            "INITIALIZATION",
            "INITIALIZATION",
            "PENDING",
            "INPROGRESS",
            "COMPLETION",
            "SUCCESSFUL",
        ]
        terminals = [False, False, False, False, False, True]
    elif mode == "all-statuses":
        actual_states = ["CREATED"] + [
            "CREATED",
            "PENDING",
            "INITIALIZATION",
            "CHECKING_APPROVAL",
            "APPROVAL_PENDING",
            "USER_INTERACTION_PENDING",
            "INPROGRESS",
            "COMPLETION",
            "SUCCESSFUL",
        ]
        terminals = [False] * 9 + [True]
    elif mode == "success-no-completed-at":
        actual_states = ["CREATED", "SUCCESSFUL"]
        terminals = [False, True]
    elif mode == "sleep-check":
        actual_states = ["CREATED", "PENDING"]
        terminals = [False, False]
    elif mode == "terminal-failures":
        actual_states = [
            "CREATED",
            "FAILED",
            "CREATED",
            "ABORTED",
            "CREATED",
            "APPROVAL_REJECTED",
        ]
        terminals = [False] * 6
    elif mode in {"invalid-delete", "non-200"}:
        actual_states = ["CREATED"] * len(entries)
        terminals = [False] * len(entries)
    else:
        actual_states = [
            value
            for _ in range(20)
            for value in ("CREATED", "SUCCESSFUL")
        ]
        terminals = [value for _ in range(20) for value in (False, True)]

    if [entry["actual_status_after"] for entry in entries] != actual_states:
        fail(f"{mode}: server-side request state sequence changed")
    if [entry["terminal_reached_after"] for entry in entries] != terminals:
        fail(f"{mode}: terminal-state transition sequence changed")


def run_case(classes: Path, temp: Path, mode: str) -> None:
    case_dir = temp / mode
    case_dir.mkdir()
    request_log = case_dir / "requests.jsonl"
    port_file = case_dir / "port.json"
    mock = subprocess.Popen(
        [
            sys.executable,
            str(PROJECT / "tests/mock_automation.py"),
            "--contract",
            str(PROJECT / "docs/contract.json"),
            "--log",
            str(request_log),
            "--port-file",
            str(port_file),
            "--mode",
            mode,
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
                server_info["deployment_id"],
                server_info["request_id"],
                str(request_log),
                mode,
            ],
            text=True,
            capture_output=True,
            timeout=15,
        )
        if run_result.returncode != 0:
            sys.stderr.write(run_result.stdout + run_result.stderr)
            fail(f"TestMain failed in {mode} mode")
        if run_result.stdout.strip() != "SUCCESSFUL":
            fail(f"unexpected TestMain output in {mode}: {run_result.stdout!r}")
    finally:
        mock.terminate()
        try:
            mock.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            mock.kill()
            mock.communicate(timeout=3)

    check_wire_log(request_log, server_info, mode)


def main() -> None:
    check_protected_files()
    check_contract_metadata()

    with tempfile.TemporaryDirectory(prefix="vcf91-0358-") as temporary:
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
                str(PROJECT / "VcfAutomationClient.java"),
                str(PROJECT / "tests/TestMain.java"),
            ],
            text=True,
            capture_output=True,
            timeout=10,
        )
        if compile_result.returncode != 0:
            sys.stderr.write(compile_result.stdout + compile_result.stderr)
            fail("Java sources did not compile")

        for mode in [
            "retry",
            "all-statuses",
            "success-no-completed-at",
            "sleep-check",
            "terminal-failures",
            "invalid-delete",
            "invalid-get",
            "non-200",
        ]:
            run_case(classes, temp, mode)

    print("PASS: reference-derived retry-safe VCF Automation delete client")


if __name__ == "__main__":
    main()
