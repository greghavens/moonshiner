#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"


def fail(message: str) -> None:
    raise SystemExit(f"verification failed: {message}")


def verify_contract() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))

    source_of_truth = contract.get("sourceOfTruth", {})
    if source_of_truth.get("type") != "reference-documentation":
        fail("contract does not identify reference documentation as its source")
    if source_of_truth.get("isPublishedSpecification") is not False:
        fail("contract must state that it is not a published specification")
    statement = source_of_truth.get("statement", "").lower()
    if "reference documentation rather than a published specification" not in statement:
        fail("contract source statement is not plain and explicit")

    operations = contract.get("operations")
    if not isinstance(operations, list):
        fail("contract operations missing")
    expected = {
        "listVSphereCloudAccounts": ("GET", "/iaas/api/cloud-accounts-vsphere"),
        "updateVSphereCloudAccountAsync": ("PATCH", "/iaas/api/cloud-accounts-vsphere/{id}"),
        "listRequestTrackers": ("GET", "/iaas/api/request-tracker"),
        "getRequestTracker": ("GET", "/iaas/api/request-tracker/{id}"),
    }
    actual = {
        item.get("operationId"): (item.get("method"), item.get("path"))
        for item in operations
        if isinstance(item, dict)
    }
    if actual != expected:
        fail(f"focused operation set changed: {actual!r}")

    pages = sources.get("sources")
    if not isinstance(pages, list) or len(pages) != len(expected):
        fail("official source index must contain one page per contract operation")
    page_by_slug = {}
    for page in pages:
        if not isinstance(page, dict):
            fail("official source entry is not an object")
        for key in ("url", "operation", "localOperationSlug", "method", "path", "fetchedAt"):
            if not isinstance(page.get(key), str) or not page[key]:
                fail(f"official source entry lacks {key}")
        if page["fetchedAt"] != "2026-08-16":
            fail("official source fetch date changed")
        if not page["url"].startswith("https://developer.broadcom.com/xapis/"):
            fail("official source is not an authoritative Broadcom xAPIs page")
        if ".invalid" in page["url"]:
            fail("official source uses an unreachable placeholder URL")
        page_by_slug[page["localOperationSlug"]] = page

    if set(page_by_slug) != set(expected):
        fail("official source operation coverage does not equal contract coverage")
    for operation in operations:
        page = page_by_slug[operation["operationId"]]
        if operation.get("source") != page["url"]:
            fail(f"source URL mismatch for {operation['operationId']}")
        if (page["method"], page["path"]) != expected[operation["operationId"]]:
            fail(f"source operation mismatch for {operation['operationId']}")


def compile_and_run() -> None:
    production = sorted((ROOT / "src").glob("*.java"))
    if production != [ROOT / "src" / "VcfAutomationCredentialRotator.java"]:
        fail("the client must remain exactly one production Java source file")

    sources = production + [
        ROOT / "tests" / "TestJson.java",
        ROOT / "tests" / "ContractMockServer.java",
        ROOT / "tests" / "TestMain.java",
    ]
    with tempfile.TemporaryDirectory(prefix="vcfa-rotation-") as classes:
        compile_result = subprocess.run(
            ["javac", "--release", "17", "-d", classes, *map(str, sources)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        if compile_result.returncode != 0:
            sys.stdout.write(compile_result.stdout)
            fail("javac failed")
        run_result = subprocess.run(
            ["java", "-cp", classes, "TestMain"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=25,
            check=False,
        )
        sys.stdout.write(run_result.stdout)
        if run_result.returncode != 0:
            fail(f"TestMain exited {run_result.returncode}")


def main() -> None:
    verify_contract()
    compile_and_run()


if __name__ == "__main__":
    main()
