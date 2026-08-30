#!/usr/bin/env python3
"""Deterministic acceptance verifier for the VCF Automation PowerShell module."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MODULE_DIR = ROOT / "VMware.Sdk.Vcf.Automation"
MANIFEST_PATH = MODULE_DIR / "VMware.Sdk.Vcf.Automation.psd1"
MODULE_PATH = MODULE_DIR / "VMware.Sdk.Vcf.Automation.psm1"
PREREQUISITE_NAME = "VMware.Sdk.Vcf.SddcManager"
PREREQUISITE_VERSION = "13.4.0.24798382"


def fail(message: str) -> None:
    raise AssertionError(message)


def deterministic_case() -> dict[str, object]:
    digest = hashlib.sha256(b"vcf90-0149-wire-case-v1").hexdigest()
    return {
        "apiToken": f"token-{digest[:19]}",
        "retryProjectId": f"retry/project {digest[20:28]}",
        "retryName": f"Core project {digest[28:36]}",
        "explicitProjectId": f"explicit project {digest[36:44]}",
        "explicitName": f"False-zero-empty {digest[44:52]}",
        "fullProjectId": f"full/project {digest[52:60]}",
        "apiVersion": "2021-07-15",
        "full": {
            "name": f"Full contract {digest[4:12]}",
            "description": f"All fields {digest[12:20]}",
            "administrators": [{"email": f"admin-{digest[:6]}@example.test"}],
            "members": [],
            "viewers": [{"email": f"viewer-{digest[6:12]}@example.test"}],
            "supervisors": [{"email": f"supervisor-{digest[12:18]}@example.test"}],
            "zoneAssignmentConfigurations": [
                {
                    "zoneId": f"zone-{digest[18:24]}",
                    "priority": 1,
                    "maxNumberInstances": 50,
                    "memoryLimitMB": 2048,
                    "cpuLimit": 8,
                    "storageLimitGB": 20,
                }
            ],
            "constraints": {
                "network": [{"mandatory": "true", "expression": f"env:{digest[24:30]}"}]
            },
            "operationTimeout": 30,
            "machineNamingTemplate": "${project.name}-${####}",
            "sharedResources": True,
            "placementPolicy": "SPREAD_MEMORY",
            "customProperties": {"tier": "gold", "empty": ""},
        },
    }


def check_seed_contract() -> dict:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    if contract.get("sourceKind") != "reference-documentation":
        fail("contract must identify reference documentation as its source kind")
    statement = contract.get("sourceStatement", "").lower()
    if "not a published specification" not in statement or "vmware/vcf-api-specs" not in statement:
        fail("contract must plainly distinguish the reference from a published specification")
    operations = contract.get("operations")
    if not isinstance(operations, list) or len(operations) != 1:
        fail("contract must name exactly one operation")
    operation = operations[0]
    if (operation.get("operationId"), operation.get("method"), operation.get("pathTemplate")) != (
        "updateProject",
        "PATCH",
        "/iaas/api/projects/{id}",
    ):
        fail("unexpected contract operation")
    source_entries = sources.get("sources")
    if not isinstance(source_entries, list) or len(source_entries) < 2:
        fail("official source inventory is incomplete")
    for entry in source_entries:
        if not entry.get("url", "").startswith("https://developer.broadcom.com/xapis/"):
            fail("official source URL must be a real Broadcom xAPIs page")
        if "Update Project" not in entry.get("operation", ""):
            fail("each source entry must state the documented operation")
        if entry.get("fetchedOn") != "2026-08-13":
            fail("each source entry must record its fetch date")
    return operation


def wait_for_ready(process: subprocess.Popen[str], ready_path: Path) -> str:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if ready_path.exists() and ready_path.stat().st_size:
            try:
                return json.loads(ready_path.read_text(encoding="utf-8"))["baseUri"]
            except (json.JSONDecodeError, KeyError):
                # The service writes a tiny file, but readers must still tolerate
                # observing it between creation and the completed write.
                pass
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            fail(f"loopback service exited early\nstdout:\n{stdout}\nstderr:\n{stderr}")
        time.sleep(0.02)
    fail("loopback service did not become ready")
    raise RuntimeError("unreachable")


def create_prerequisite_fixture(temp: Path) -> Path:
    """Provide only enough module metadata for the protected manifest to import.

    The deliverable must retain its real PowerCLI dependency without vendoring it.
    Acceptance tests therefore supply an isolated, behavior-free prerequisite so
    candidate HTTP behavior can be verified on machines without PowerCLI.
    """
    module_root = temp / "powershell-modules"
    version_dir = module_root / PREREQUISITE_NAME / PREREQUISITE_VERSION
    version_dir.mkdir(parents=True)
    (version_dir / f"{PREREQUISITE_NAME}.psm1").write_text(
        "Set-StrictMode -Version Latest\n", encoding="utf-8"
    )
    (version_dir / f"{PREREQUISITE_NAME}.psd1").write_text(
        "@{\n"
        f"    RootModule = '{PREREQUISITE_NAME}.psm1'\n"
        f"    ModuleVersion = '{PREREQUISITE_VERSION}'\n"
        "    GUID = 'a1584e61-6b88-4a79-87fa-f9c1312b2f91'\n"
        "    FunctionsToExport = @()\n"
        "    CmdletsToExport = @()\n"
        "    VariablesToExport = @()\n"
        "    AliasesToExport = @()\n"
        "}\n",
        encoding="utf-8",
    )
    return module_root


def assert_prerequisite_not_vendored() -> None:
    vendored = [
        path.relative_to(ROOT)
        for path in ROOT.rglob("*")
        if path.name.casefold() == PREREQUISITE_NAME.casefold()
        or (path.is_file() and path.stem.casefold() == PREREQUISITE_NAME.casefold())
    ]
    if vendored:
        fail(f"PowerCLI prerequisite was vendored into the deliverable: {vendored!r}")


def assert_unserved_operations(base_uri: str, encoded_id: str) -> None:
    split = urlsplit(base_uri)
    connection = http.client.HTTPConnection(split.hostname, split.port, timeout=3)
    try:
        connection.request("GET", f"/iaas/api/projects/{encoded_id}")
        response = connection.getresponse()
        response.read()
        if response.status != 405:
            fail(f"contract path unexpectedly served GET with status {response.status}")
        connection.request("POST", "/not-in-contract")
        response = connection.getresponse()
        response.read()
        if response.status != 404:
            fail(f"unknown path unexpectedly served with status {response.status}")
    finally:
        connection.close()


def run_candidate(base_uri: str, case_path: Path, prerequisite_root: Path) -> None:
    if not MODULE_PATH.is_file():
        fail(f"missing implementation: {MODULE_PATH.relative_to(ROOT)}")
    command = [
        "pwsh",
        "-NoLogo",
        "-NoProfile",
        "-File",
        str(ROOT / "tests" / "invoke_candidate.ps1"),
        "-ModuleManifest",
        str(MANIFEST_PATH),
        "-Server",
        base_uri,
        "-CaseFile",
        str(case_path),
    ]
    environment = os.environ.copy()
    existing_module_path = environment.get("PSModulePath", "")
    environment["PSModulePath"] = str(prerequisite_root) + (
        os.pathsep + existing_module_path if existing_module_path else ""
    )
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=45,
        env=environment,
    )
    if completed.returncode != 0:
        fail(
            "candidate PowerShell invocation failed\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )


def check_wire_log(log_path: Path, case: dict[str, object], operation: dict) -> None:
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    if len(records) != 4:
        fail(f"expected exactly 4 contract requests, observed {len(records)}")

    expected_retry_path = "/iaas/api/projects/" + quote(case["retryProjectId"], safe="")
    expected_explicit_path = "/iaas/api/projects/" + quote(case["explicitProjectId"], safe="")
    expected_full_path = "/iaas/api/projects/" + quote(case["fullProjectId"], safe="")
    required_headers = {
        "authorization": f"Bearer {case['apiToken']}",
        "accept": "application/json",
    }

    for index, record in enumerate(records):
        if record["method"] != operation["method"]:
            fail(f"request {index + 1} used {record['method']} instead of {operation['method']}")
        for header, expected in required_headers.items():
            actual = record["headers"].get(header)
            if actual != expected:
                fail(f"request {index + 1} header {header!r}: expected {expected!r}, got {actual!r}")
        content_type = record["headers"].get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != operation["request"]["mediaType"]:
            fail(
                f"request {index + 1} content media type: "
                f"expected {operation['request']['mediaType']!r}, got {content_type!r}"
            )

    minimal_body = {"name": case["retryName"]}
    for index in (0, 1):
        record = records[index]
        if record["path"] != expected_retry_path or record["rawPath"] != expected_retry_path:
            fail(f"request {index + 1} did not encode Id as one path segment: {record['rawPath']!r}")
        if record["query"] != {}:
            fail(f"request {index + 1} sent unset optional query parameters: {record['query']!r}")
        if record["body"] != minimal_body:
            fail(f"request {index + 1} did not omit unset optional fields: {record['bodyText']!r}")

    if records[0]["effectApplied"] is not True or records[0]["effectCount"] != 1:
        fail("first state-setting PATCH did not apply exactly one effect")
    if records[1]["effectApplied"] is not False or records[1]["effectCount"] != 1:
        fail("identical retry duplicated the mutation effect")
    if records[0]["bodyText"] != records[1]["bodyText"] or records[0]["rawPath"] != records[1]["rawPath"]:
        fail("retry did not send the identical state-setting request")

    explicit_body = {
        "name": case["explicitName"],
        "description": "",
        "operationTimeout": 0,
        "sharedResources": False,
    }
    third = records[2]
    if third["path"] != expected_explicit_path:
        fail(f"explicit-values request encoded the wrong path: {third['path']!r}")
    if third["query"] != {"apiVersion": [case["apiVersion"]], "validatePrincipals": ["false"]}:
        fail(f"explicit-values request parsed to the wrong query: {third['query']!r}")
    if third["body"] != explicit_body:
        fail(f"false, zero, and explicit empty string were not preserved exactly: {third['bodyText']!r}")
    unexpected = set(third["body"]) - set(explicit_body)
    if unexpected:
        fail(f"explicit-values request sent other unset optional properties: {sorted(unexpected)!r}")

    full_body = case["full"]
    fourth = records[3]
    if fourth["path"] != expected_full_path:
        fail(f"full-contract request encoded the wrong path: {fourth['path']!r}")
    if fourth["body"] != full_body:
        fail(f"full-contract body did not serialize every optional field exactly: {fourth['bodyText']!r}")
    if fourth["query"] != {"apiVersion": [case["apiVersion"]], "validatePrincipals": ["true"]}:
        fail(f"full-contract request parsed to the wrong query: {fourth['query']!r}")


def main() -> None:
    operation = check_seed_contract()
    assert_prerequisite_not_vendored()
    case = deterministic_case()
    with tempfile.TemporaryDirectory(prefix="vcf90-0149-") as temp_name:
        temp = Path(temp_name)
        ready_path = temp / "ready.json"
        log_path = temp / "requests.jsonl"
        case_path = temp / "case.json"
        prerequisite_root = create_prerequisite_fixture(temp)
        case_path.write_text(json.dumps(case, separators=(",", ":")), encoding="utf-8")
        service = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "tests" / "mock_vcf_automation.py"),
                "--contract",
                str(CONTRACT_PATH),
                "--request-log",
                str(log_path),
                "--ready-file",
                str(ready_path),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            base_uri = wait_for_ready(service, ready_path)
            run_candidate(base_uri, case_path, prerequisite_root)
            assert_unserved_operations(base_uri, quote(case["retryProjectId"], safe=""))
        finally:
            service.terminate()
            try:
                service.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                service.kill()
                service.communicate(timeout=5)
        check_wire_log(log_path, case, operation)
    print("PASS: VCF Automation update module matches the reference-derived wire contract")


if __name__ == "__main__":
    main()
