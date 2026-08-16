#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True

from contract_mock import ContractMockServer


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise AssertionError(message)


def validate_documentation() -> None:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))

    statement = contract.get("provenance", {}).get("statement", "").lower()
    if "reference documentation" not in statement or "not a published specification" not in statement:
        fail("contract must plainly identify itself as reference documentation, not a published specification")

    operations = contract.get("operations", [])
    operation_names = {f'{operation["method"]} {operation["referenceOperation"]}' for operation in operations}
    source_names = {source["operation"] for source in sources.get("sources", [])}
    if operation_names != source_names:
        fail(f"official source operations do not match contract: {operation_names} != {source_names}")
    if not sources.get("sources"):
        fail("official_sources.json has no sources")
    for source in sources["sources"]:
        if not source.get("url", "").startswith("https://developer.broadcom.com/xapis/"):
            fail(f"source is not an official Broadcom xAPIs page: {source}")
        if source.get("fetchedOn") != "2026-08-16":
            fail(f"source fetch date is not pinned: {source}")


def compile_client(classes: Path) -> None:
    command = [
        "javac",
        "--release",
        "17",
        "-d",
        str(classes),
        str(ROOT / "src" / "VcfAutomationDiagnostic.java"),
        str(ROOT / "tests" / "TestMain.java"),
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=15)
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        fail("Java compilation failed")


def run_harness(classes: Path, base_url: str) -> str:
    result = subprocess.run(
        ["java", "-cp", str(classes), "TestMain", base_url],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        fail("TestMain failed")
    if not result.stdout.startswith("DIAGNOSIS_OK\n"):
        fail(f"unexpected TestMain output: {result.stdout!r}")
    return result.stdout


def validate_request_log(server: ContractMockServer) -> None:
    records = list(server.state.request_log)
    if not records:
        fail("client made no requests")
    if any(record.authorization != "Bearer fixture-token" for record in records):
        fail(f"every request must carry the configured bearer token: {records}")
    if any(record.operation_id is None for record in records):
        fail(f"client called an operation outside docs/contract.json: {records}")

    counts = Counter(record.operation_id for record in records)
    required = {
        "getRequest",
        "getRequestEvents",
        "getEventLogs",
        "getEventLogsContent",
    }
    missing = required - counts.keys()
    if missing:
        fail(f"client did not use all diagnostic operations: {sorted(missing)}")

    paths = {record.path for record in records}
    expected_evidence_paths = {
        "/deployment/api/requests/req-failed-42/events/evt-allocate/logs",
        "/deployment/api/requests/req-failed-42/events/evt-allocate/logs/download",
        "/deployment/api/requests/req-failed-42/events/evt-cleanup/logs",
        "/deployment/api/requests/req-failed-42/events/evt-cleanup/logs/download",
    }
    missing_paths = expected_evidence_paths - paths
    if missing_paths:
        fail(f"client did not pull all hasLogs evidence: {sorted(missing_paths)}")

    forbidden_no_log_paths = {
        "/deployment/api/requests/req-failed-42/events/evt-start/logs",
        "/deployment/api/requests/req-failed-42/events/evt-start/logs/download",
        "/deployment/api/requests/req-failed-42/events/evt-allocate-z/logs",
        "/deployment/api/requests/req-failed-42/events/evt-allocate-z/logs/download",
    }
    called_no_log_paths = forbidden_no_log_paths & paths
    if called_no_log_paths:
        fail(f"client fetched logs for hasLogs=false event: {sorted(called_no_log_paths)}")


def main() -> None:
    validate_documentation()
    if shutil.which("javac") is None or shutil.which("java") is None:
        fail("a JDK is required")

    with tempfile.TemporaryDirectory(prefix="vcf91-0346-") as temp:
        classes = Path(temp) / "classes"
        classes.mkdir()
        compile_client(classes)

        server = ContractMockServer()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            first = run_harness(classes, server.base_url)
            second = run_harness(classes, server.base_url)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        if first != second:
            fail("diagnostic output changed when the mock flipped collection response order")
        validate_request_log(server)

    print("verification passed")


if __name__ == "__main__":
    main()
