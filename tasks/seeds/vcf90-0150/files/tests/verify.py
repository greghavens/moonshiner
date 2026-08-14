#!/usr/bin/env python3
"""Protected deterministic verifier for the VCF Automation module."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from mock_vcfa import EXPECTED_OPERATIONS, REQUEST_ID, SUCCESS_REQUEST_ID, start_server


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MODULE_PATH = ROOT / "Vcf.Automation" / "Vcf.Automation.psm1"
SCENARIO_PATH = ROOT / "tests" / "invoke_scenario.ps1"
DEPLOYMENT_ID = "11111111-1111-4111-8111-111111111111"
BOUND_DEPLOYMENT_ID = "33333333-3333-4333-8333-333333333333"


def check_provenance(issues: list[str]) -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    statement = contract.get("source", {}).get("statement", "")
    if contract.get("source", {}).get("kind") != "reference-documentation":
        issues.append("contract source kind is not reference-documentation")
    if "not from a published specification" not in statement:
        issues.append("contract does not state plainly that it is reference-derived rather than a published specification")
    if "vmware/vcf-api-specs" not in statement:
        issues.append("contract does not record the absence of a VCF Automation specification in vmware/vcf-api-specs")

    operations = {item["documented_operation"] for item in contract["operations"]}
    source_operations = {item["operation"] for item in sources["sources"]}
    if operations != source_operations:
        issues.append(f"official source operation coverage differs: {source_operations!r} != {operations!r}")
    if len(sources["sources"]) != 3:
        issues.append("official_sources.json must record one page for each of the three operations")
    for item in sources["sources"]:
        if not item["url"].startswith("https://developer.broadcom.com/xapis/") or "/9.0/" not in item["url"]:
            issues.append(f"source is not a VCF Automation 9.0 Broadcom xAPIs page: {item['url']}")
        if item.get("fetched_on") != "2026-08-13":
            issues.append(f"source fetch date is not pinned: {item!r}")

    operation_names = {item["name"] for item in contract["operations"]}
    if operation_names != EXPECTED_OPERATIONS:
        issues.append(f"contract operation names changed: {operation_names!r}")


def invoke_candidate(server_url: str):
    environment = os.environ.copy()
    environment["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
    return subprocess.run(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(SCENARIO_PATH),
            "-ModulePath",
            str(MODULE_PATH),
            "-ServerUri",
            server_url,
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )


def parse_result(stdout: str):
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith(("{", "[")):
            return json.loads(line)
    return None


def read_log(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def check_wire(records: list[dict], issues: list[str]) -> None:
    expected = [
        {
            "method": "PATCH",
            "path": f"/deployment/api/deployments/{DEPLOYMENT_ID}",
            "operation": "Patch Deployment",
            "body": {"name": "payments-prod-renamed"},
            "has_content_type": True,
        },
        {
            "method": "POST",
            "path": f"/deployment/api/deployments/{DEPLOYMENT_ID}/requests",
            "operation": "Submit Deployment Action Request",
            "body": {
                "actionId": "Deployment.ChangeLease",
                "inputs": {"Lease Expiration Date": "2026-09-30T00:00:00Z"},
            },
            "has_content_type": True,
        },
        {
            "method": "GET",
            "path": f"/deployment/api/requests/{REQUEST_ID}",
            "operation": "Get Request",
            "body": None,
            "has_content_type": False,
        },
        {
            "method": "PATCH",
            "path": f"/deployment/api/deployments/{BOUND_DEPLOYMENT_ID}",
            "operation": "Patch Deployment",
            "body": {
                "name": "payments-dev-renamed",
                "description": "",
                "iconId": "44444444-4444-4444-8444-444444444444",
            },
            "has_content_type": True,
        },
        {
            "method": "POST",
            "path": f"/deployment/api/deployments/{BOUND_DEPLOYMENT_ID}/requests",
            "operation": "Submit Deployment Action Request",
            "body": {
                "actionId": "Deployment.ChangeOwner",
                "inputs": {"Owner": "fixture-owner"},
                "reason": "",
            },
            "has_content_type": True,
        },
        {
            "method": "GET",
            "path": f"/deployment/api/requests/{SUCCESS_REQUEST_ID}",
            "operation": "Get Request",
            "body": None,
            "has_content_type": False,
        },
    ]
    if len(records) < len(expected):
        issues.append(f"expected six candidate requests, got {len(records)}")
        return

    for index, (actual, wanted) in enumerate(zip(records[: len(expected)], expected), start=1):
        for key in ("method", "path"):
            if actual.get(key) != wanted[key]:
                issues.append(f"request {index} {key}: {actual.get(key)!r} != {wanted[key]!r}")
        if actual.get("query") != "":
            issues.append(f"request {index} unexpectedly used query {actual.get('query')!r}")
        if actual.get("matched_operation") != wanted["operation"]:
            issues.append(f"request {index} matched {actual.get('matched_operation')!r}, expected {wanted['operation']!r}")
        raw_body = actual.get("body", "")
        try:
            body = json.loads(raw_body) if raw_body else None
        except json.JSONDecodeError:
            body = "<invalid JSON>"
        if body != wanted["body"]:
            issues.append(f"request {index} JSON body: {body!r} != {wanted['body']!r}")
        headers = actual.get("headers", {})
        if headers.get("authorization") != "Bearer fixture-token":
            issues.append(f"request {index} bearer header was {headers.get('authorization')!r}")
        if headers.get("accept") != "application/json":
            issues.append(f"request {index} Accept header was {headers.get('accept')!r}")
        content_type = headers.get("content-type")
        if wanted["has_content_type"] and content_type != "application/json":
            issues.append(f"request {index} Content-Type was {content_type!r}")
        if not wanted["has_content_type"] and content_type is not None:
            issues.append(f"bodyless request {index} unexpectedly sent Content-Type {content_type!r}")


def check_one_report(result, expected_top: dict, expected_steps: list[tuple], issues: list[str]) -> None:
    if not isinstance(result, dict):
        issues.append("command did not return a JSON change report")
        return
    for key, value in expected_top.items():
        if result.get(key) != value:
            issues.append(f"report {key}: {result.get(key)!r} != {value!r}")

    steps = result.get("Steps")
    if not isinstance(steps, list) or len(steps) != 3:
        issues.append(f"report Steps was not the required three-step list: {steps!r}")
        return
    fields = ("Operation", "State", "HttpStatus", "RemoteStatus", "Details")
    for index, (step, values) in enumerate(zip(steps, expected_steps), start=1):
        actual = tuple(step.get(field) for field in fields)
        if actual != values:
            issues.append(f"report step {index}: {actual!r} != {values!r}")


def check_report(result, issues: list[str]) -> None:
    if not isinstance(result, list) or len(result) != 2:
        issues.append(f"command did not return the two expected change reports: {result!r}")
        return

    check_one_report(
        result[0],
        {
            "DeploymentId": DEPLOYMENT_ID,
            "RequestId": REQUEST_ID,
            "Succeeded": False,
            "OverallStatus": "Failed",
        },
        [
            ("Patch Deployment", "Succeeded", 200, "UPDATE_SUCCESSFUL", None),
            ("Submit Deployment Action Request", "Succeeded", 200, "CREATED", None),
            ("Get Request", "Failed", 200, "FAILED", "Cloud provider rejected lease change"),
        ],
        issues,
    )
    check_one_report(
        result[1],
        {
            "DeploymentId": BOUND_DEPLOYMENT_ID,
            "RequestId": SUCCESS_REQUEST_ID,
            "Succeeded": True,
            "OverallStatus": "Succeeded",
        },
        [
            ("Patch Deployment", "Succeeded", 200, "UPDATE_SUCCESSFUL", None),
            ("Submit Deployment Action Request", "Succeeded", 200, "CREATED", None),
            ("Get Request", "Succeeded", 200, "SUCCESSFUL", None),
        ],
        issues,
    )


def main() -> int:
    issues: list[str] = []
    check_provenance(issues)

    with tempfile.TemporaryDirectory(prefix="vcfa-contract-") as temporary:
        log_path = Path(temporary) / "requests.jsonl"
        server, thread = start_server(CONTRACT_PATH, log_path)
        try:
            completed = invoke_candidate(server.url)
            result = parse_result(completed.stdout)

            try:
                urlopen(f"{server.url}/deployment/api/deployments", timeout=5)
                issues.append("mock served an operation absent from the pinned contract")
            except HTTPError as error:
                if error.code != 404:
                    issues.append(f"uncontracted mock operation returned HTTP {error.code}, expected 404")

            records = read_log(log_path)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    if completed.returncode != 0:
        issues.append(
            "PowerShell scenario failed instead of returning the partial-failure report: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    check_wire(records, issues)
    check_report(result, issues)

    if not records or records[-1].get("matched_operation") is not None:
        issues.append("mock request log did not record the rejected uncontracted probe")
    elif len(records) != 7:
        issues.append(f"mock request log contained {len(records)} records, expected six candidate calls and one rejected probe")

    if issues:
        for issue in issues:
            print(f"FAIL: {issue}", file=sys.stderr)
        return 1
    print("PASS: VCF Automation reference contract, exact wire shape, and partial-failure report")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
