#!/usr/bin/env python3
"""Deterministic protected verifier for VcfFailureDiagnosticsClient."""

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
COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
EXPECTED_OPERATIONS = [
    ("getTask", "GET", "/v1/tasks/{id}"),
    ("getNotifications", "GET", "/v1/notifications"),
    ("startSupportBundle", "POST", "/v1/system/support-bundles"),
    ("getSupportBundleStatus", "GET", "/v1/system/support-bundles/{id}"),
    (
        "exportSupportBundleByID",
        "GET",
        "/v1/system/support-bundles/{id}/data",
    ),
]
PROTECTED_HASHES = {
    "docs/contract.json": "b7ed7819d00d2ed99b9573ae8cb0a92713240db5d4f48a2d5cf2838eb093a136",
    "docs/official_sources.json": "295b93aa5721b5985bbda1828daeb26881679c9b2dbb77dd5f9e533357a5ff7f",
    "tests/TestMain.java": "bcf595c462dab41cd1b82d86bf17c0478bb2c7d8608f3c62ef8abb09382e0416",
    "tests/mock_server.py": "d952da6471106df2ab5bacd9661fa65c392e198e6633c0df678a3b1c3f649c50",
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
        contract["derived_from"]["repository_commit_sha"] != COMMIT
        or sources["repository"]["commit_sha"] != COMMIT
        or contract["derived_from"]["spec_path"] != SPEC_PATH
        or sources["specification"]["path"] != SPEC_PATH
    ):
        fail("official specification provenance changed")
    if (
        contract["derived_from"]["info_version"] != "9.1.0.0"
        or sources["repository"]["license"] != "Apache-2.0"
    ):
        fail("product version or license provenance changed")

    projected = [
        (item["operationId"], item["method"], item["path"])
        for item in contract["operations"]
    ]
    recorded = [
        (item["operationId"], item["method"], item["path"])
        for item in sources["operations"]
    ]
    if projected != EXPECTED_OPERATIONS or recorded != EXPECTED_OPERATIONS:
        fail("operationId projection changed")
    for operation in sources["operations"]:
        if (
            operation["repository_commit_sha"] != COMMIT
            or operation["spec_path"] != SPEC_PATH
        ):
            fail("operation provenance is not recorded at commit granularity")

    schemas = contract["schemas"]
    if schemas["SupportBundleSpec"]["required"] != []:
        fail("SupportBundleSpec optionality changed")
    if set(schemas["SupportBundleSpec"]["properties"]) != {
        "options",
        "scope",
        "logs",
    }:
        fail("SupportBundleSpec projection changed")
    expected_logs = {
        "vcLogs",
        "nsxLogs",
        "esxLogs",
        "hcxLogs",
        "wcpLogs",
        "sddcManagerLogs",
        "apiLogs",
        "systemDebugLogs",
        "vmScreenshots",
        "vraLogs",
        "vropsLogs",
        "vrliLogs",
        "vrslcmLogs",
        "automationLogs",
        "operationsLogs",
        "operationsForLogs",
        "lifecycleLogs",
        "vmsLogs",
    }
    if set(schemas["Logs"]["properties"]) != expected_logs:
        fail("Logs property projection changed")
    if schemas["Task"]["required"] != [
        "creationTimestamp",
        "id",
        "name",
        "status",
    ]:
        fail("Task required fields changed")
    if schemas["Resource"]["required"] != ["resourceId", "type"]:
        fail("Resource required fields changed")


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


def check_wire_log(log_path: Path, info: dict) -> None:
    entries = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(entries) != 6:
        fail(f"expected six evidence-chain requests, got {len(entries)}")
    if [entry["sequence"] for entry in entries] != list(range(1, 7)):
        fail("request sequence is not deterministic")

    task_path = "/v1/tasks/" + quote(info["task_id"], safe="")
    bundle_path = "/v1/system/support-bundles/" + quote(
        info["bundle_id"], safe=""
    )
    expected = [
        ("getTask", "GET", task_path),
        ("getNotifications", "GET", "/v1/notifications"),
        ("startSupportBundle", "POST", "/v1/system/support-bundles"),
        ("getSupportBundleStatus", "GET", bundle_path),
        ("getSupportBundleStatus", "GET", bundle_path),
        ("exportSupportBundleByID", "GET", bundle_path + "/data"),
    ]
    for index, (entry, wanted) in enumerate(zip(entries, expected, strict=True)):
        actual = (entry["operationId"], entry["method"], entry["target"])
        if actual != wanted:
            fail(f"request {index + 1} target mismatch: {actual!r} != {wanted!r}")
        if entry["path"] != wanted[2] or entry["query"] != "" or "?" in entry["target"]:
            fail(f"request {index + 1} sent an unexpected query: {entry!r}")
        headers = entry["headers"]
        if headers.get("authorization") != "Bearer " + info["access_token"]:
            fail(f"request {index + 1} used the wrong bearer token")
        wanted_accept = (
            "application/octet-stream" if index == 5 else "application/json"
        )
        if headers.get("accept") != wanted_accept:
            fail(f"request {index + 1} sent the wrong Accept header")

        if index == 2:
            if headers.get("content-type") != "application/json":
                fail("startSupportBundle must send Content-Type: application/json")
            if headers.get("content-length") != str(entry["bodyLength"]):
                fail("startSupportBundle Content-Length is wrong")
            if "transfer-encoding" in headers:
                fail("startSupportBundle must not use transfer encoding")
        else:
            if (
                entry["body"] != ""
                or entry["bodyLength"] != 0
                or "content-type" in headers
                or "transfer-encoding" in headers
            ):
                fail(f"GET request {index + 1} unexpectedly carried an entity")

    body = entries[2]["body"]
    expected_body = '{"logs":{"sddcManagerLogs":true,"apiLogs":true}}'
    if body != expected_body:
        fail(f"wrong compact support-bundle body: {body!r}")
    try:
        document = json.loads(body)
    except json.JSONDecodeError as error:
        fail(f"support-bundle body is not JSON: {error}")
    if document != {
        "logs": {"sddcManagerLogs": True, "apiLogs": True},
    }:
        fail(f"wrong SupportBundleSpec wire shape: {document!r}")
    absent = {
        "options",
        "scope",
        "vcLogs",
        "nsxLogs",
        "esxLogs",
        "hcxLogs",
        "wcpLogs",
        "systemDebugLogs",
        "vmScreenshots",
        "vraLogs",
        "vropsLogs",
        "vrliLogs",
        "vrslcmLogs",
        "automationLogs",
        "operationsLogs",
        "operationsForLogs",
        "lifecycleLogs",
        "vmsLogs",
    }
    if absent.intersection(document) or absent.intersection(document["logs"]):
        fail("unset optional fields must be omitted rather than sent empty")
    if "null" in body or ":false" in body or ':""' in body:
        fail("unset optional values appeared as JSON placeholders")


def main() -> None:
    check_protected_files()
    check_contract_metadata()

    with tempfile.TemporaryDirectory(prefix="vcf91-0044-") as temporary:
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
                str(PROJECT / "VcfFailureDiagnosticsClient.java"),
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
        port_file = temp / "port.json"
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
            info = wait_for_server(port_file, mock)
            run_result = subprocess.run(
                [
                    "java",
                    "-cp",
                    str(classes),
                    "TestMain",
                    f"http://127.0.0.1:{info['port']}/",
                    info["access_token"],
                    info["task_id"],
                    info["event_id"],
                    info["cause"],
                    info["bundle_id"],
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

        check_wire_log(request_log, info)

    print("PASS: correlated SDDC Manager failure evidence")


if __name__ == "__main__":
    main()
