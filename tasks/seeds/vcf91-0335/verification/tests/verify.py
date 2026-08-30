#!/usr/bin/env python3
"""Protected deterministic verifier for vcf91-0335.

Checks three things, offline: the reference-derived contract and its provenance are intact,
the client is still a single Java source file, and the loopback acceptance harness passes.
No VMware endpoint and no network is contacted.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PORTAL = "https://developer.broadcom.com/xapis"
FETCH_DATE = "2026-08-11"
EXPECTED_OPERATIONS = {
    "exchangeRefreshToken": ("POST", "/tm/oauth/tenant/{tenant}/token"),
    "createProject": ("POST", "/iaas/api/projects"),
    "requestCatalogItemInstances": ("POST", "/catalog/api/items/{id}/request"),
    "getDeploymentById": ("GET", "/deployment/api/deployments/{deploymentId}"),
    "submitDeploymentActionRequest": ("POST", "/deployment/api/deployments/{deploymentId}/requests"),
    "getRequest": ("GET", "/deployment/api/requests/{requestId}"),
}
PROTECTED_SHA256 = {
    "docs/contract.json": "56251e3c6184fbcd140c0c5347c5d1ba6e4771023c91fb445b9f09e68546a901",
    "docs/official_sources.json": "4881dce99952d80d41b2d671358576f81947510e1a9bdbd08371fa1a274344f0",
    "tests/MockVcfaServer.java": "ebc3eab8bc7a1ce8b7a3d23e700832cd288aa7f2851a5033a4f7b76ab6ca0fd8",
    "tests/TestMain.java": "41ef11adaf54acc06249e84475a1a0d3acb10152a14ff7b80c9486517510d139",
}
SUCCESS_MARKER = "PASS: VCF Automation change wire shape and failure reporting verified"


def fail(message: str) -> None:
    raise SystemExit(f"VERIFY FAILED: {message}")


def check_protected_files() -> None:
    for relative, expected in PROTECTED_SHA256.items():
        path = ROOT / relative
        if not path.is_file():
            fail(f"protected fixture missing: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            fail(f"protected fixture changed: {relative}")


def check_contract() -> None:
    contract = json.loads((ROOT / "docs/contract.json").read_text(encoding="utf-8"))
    source = contract.get("source", {})
    if source.get("kind") != "reference-documentation":
        fail("contract no longer declares a reference-documentation origin")
    if source.get("is_published_specification") is not False:
        fail("contract must state plainly that it is not a published specification")
    if source.get("specification_repository_publishes_vcf_automation") is not False:
        fail("contract must record that vcf-api-specs publishes no VCF Automation specification")
    if source.get("portal") != PORTAL:
        fail("contract source portal changed")
    if source.get("product_version") != "9.1":
        fail("contract is no longer pinned to VCF Automation 9.1")
    statement = source.get("statement", "").lower()
    if "not derived from a published api specification" not in statement:
        fail("contract statement no longer says its source is reference documentation")

    operations = contract.get("operations", [])
    actual = {
        operation.get("operationId"): (operation.get("method"), operation.get("path"))
        for operation in operations
    }
    if actual != EXPECTED_OPERATIONS or len(operations) != len(EXPECTED_OPERATIONS):
        fail("contract operation set changed")

    rules = contract.get("wire_rules", {})
    omission = rules.get("optional_field_omission", "")
    if "must be omitted" not in omission or "empty object" not in omission:
        fail("contract no longer requires unset optional fields to be omitted")
    if not rules.get("field_order"):
        fail("contract no longer pins request field order")

    polling = contract.get("polling", {})
    if polling.get("deployment_terminal_statuses") is None or polling.get(
        "request_terminal_statuses"
    ) is None:
        fail("contract no longer records the terminal status vocabularies")
    if "HTTP status alone" not in polling.get("request_failure_note", ""):
        fail("contract no longer warns that HTTP status alone does not prove success")


def check_sources() -> None:
    sources = json.loads((ROOT / "docs/official_sources.json").read_text(encoding="utf-8"))
    if sources.get("derived_artifact") != "docs/contract.json":
        fail("official sources no longer describe docs/contract.json")
    if sources.get("source_kind") != "reference-documentation":
        fail("official sources no longer declare a reference-documentation origin")

    repository = sources.get("why_not_a_specification", {})
    if repository.get("repository") != "https://github.com/vmware/vcf-api-specs":
        fail("official sources must record which specification repository was checked")
    if repository.get("publishes_vcf_automation_specification") is not False:
        fail("official sources must record that the repository has no VCF Automation spec")
    if repository.get("license") != "Apache-2.0":
        fail("official sources must record the repository license")

    pages = sources.get("pages", [])
    if len(pages) != len(EXPECTED_OPERATIONS):
        fail("official sources must record one reference page per contract operation")
    for page in pages:
        operation_id = page.get("operationId")
        expected = EXPECTED_OPERATIONS.get(operation_id)
        if expected is None:
            fail(f"unrecognized operationId in official sources: {operation_id}")
        if (page.get("method", "").upper(), page.get("path")) != expected:
            fail(f"operation {operation_id} source mapping changed")
        url = page.get("url", "")
        if not url.startswith(PORTAL + "/") or not url.endswith("/"):
            fail(f"operation {operation_id} does not cite an xAPIs reference page URL: {url}")
        if page.get("date_fetched") != FETCH_DATE:
            fail(f"operation {operation_id} does not record the date its page was fetched")
        if not page.get("documents"):
            fail(f"operation {operation_id} does not say what its page documents")


def compile_and_run() -> None:
    production = sorted((ROOT / "src").glob("*.java"))
    if production != [ROOT / "src/VcfaChangeClient.java"]:
        fail("the client must remain a single Java source file: src/VcfaChangeClient.java")

    sources = [
        ROOT / "src/VcfaChangeClient.java",
        ROOT / "tests/MockVcfaServer.java",
        ROOT / "tests/TestMain.java",
    ]
    with tempfile.TemporaryDirectory(prefix="vcf91-0335-") as output:
        compile_result = subprocess.run(
            ["javac", "--release", "17", "-d", output, *map(str, sources)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if compile_result.returncode != 0:
            fail("javac failed:\n" + compile_result.stdout + compile_result.stderr)

        run_result = subprocess.run(
            ["java", "-ea", "-cp", output, "TestMain", str(ROOT / "docs/contract.json")],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
        if run_result.returncode != 0:
            fail("TestMain failed:\n" + run_result.stdout + run_result.stderr)
        if SUCCESS_MARKER not in run_result.stdout:
            fail("TestMain did not emit its success marker")


def main() -> None:
    check_protected_files()
    check_contract()
    check_sources()
    compile_and_run()
    print("PASS: vcf91-0335")


if __name__ == "__main__":
    main()
