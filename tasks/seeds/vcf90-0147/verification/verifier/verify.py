from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from mock_vcfa import start_mock


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MODULE_MANIFEST = ROOT / "VcfAutomation" / "VcfAutomation.psd1"
JOB_PATH = ROOT / "verifier" / "job.json"
RUNNER_PATH = ROOT / "verifier" / "run_fixture.ps1"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_documentation_contract() -> dict[str, Any]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    source = contract["source"]
    require(source["kind"] == "reference-documentation", "contract source kind must be reference-documentation")
    require("not a published specification" in source["statement"], "contract must disclaim a published specification")
    require("vmware/vcf-api-specs" in source["specificationRepositoryNote"], "contract must record the specification gap")
    require(contract["scenarioProfile"]["expiredAccessTokenStatus"] == 401, "expiry scenario must use HTTP 401")

    operations = contract["operations"]
    named = {(item["method"], item["path"], item["referenceOperation"]) for item in operations}
    require(
        named
        == {
            ("POST", "/iaas/api/login", "Retrieve Auth Token"),
            ("PATCH", "/iaas/api/projects/{id}", "Update Project"),
        },
        "contract operations changed",
    )

    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))
    require(len(sources) == 2, "source ledger must contain exactly the pages used by this contract")
    source_operations = {
        (item["operation"]["method"], item["operation"]["path"], item["operation"]["name"])
        for item in sources
    }
    require(source_operations == named, "every contract operation must map to one source page")
    for item in sources:
        require(item["fetchedOn"] == "2026-08-13", "each source needs its fetch date")
        require(item["url"].startswith("https://developer.broadcom.com/xapis/"), "sources must be Broadcom xAPIs pages")
        require("/9.0/" in item["url"], "source URLs must be pinned to VCF Automation 9.0")
    return contract


def read_log(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def invoke_fixture(pwsh: str, server: Any, result_path: Path) -> subprocess.CompletedProcess[str]:
    host, port = server.server_address
    environment = os.environ.copy()
    environment["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
    return subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(RUNNER_PATH),
            "-ModulePath",
            str(MODULE_MANIFEST),
            "-JobPath",
            str(JOB_PATH),
            "-Server",
            f"http://{host}:{port}",
            "-ResultPath",
            str(result_path),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )


def verify_wire(log: list[dict[str, Any]]) -> None:
    expected = [
        (
            "POST",
            "/iaas/api/login?apiVersion=2021-07-15",
            None,
            {"refreshToken": "fixture-refresh-token"},
        ),
        (
            "PATCH",
            "/iaas/api/projects/project-alpha?apiVersion=2021-07-15",
            "Bearer access-token-one",
            {
                "name": "platform-alpha",
                "description": "Managed by VCF 9",
                "administrators": [{"email": "admin@foundation.example"}],
                "members": [{"email": "member@foundation.example"}],
                "viewers": [{"email": "viewer@foundation.example"}],
                "supervisors": [{"email": "supervisor@foundation.example"}],
                "zoneAssignmentConfigurations": [{"zoneId": "zone-foundation", "priority": 1}],
                "constraints": {
                    "network": [{"expression": "environment:production", "mandatory": True}]
                },
                "operationTimeout": 0,
                "machineNamingTemplate": "foundation-${####}",
                "sharedResources": False,
                "placementPolicy": "SPREAD",
                "customProperties": {"costCenter": "foundation"},
            },
        ),
        (
            "PATCH",
            "/iaas/api/projects/project%20beta%2Fblue?apiVersion=2021-07-15",
            "Bearer access-token-one",
            {"name": "platform-beta"},
        ),
        (
            "POST",
            "/iaas/api/login?apiVersion=2021-07-15",
            None,
            {"refreshToken": "fixture-refresh-token"},
        ),
        (
            "PATCH",
            "/iaas/api/projects/project%20beta%2Fblue?apiVersion=2021-07-15",
            "Bearer access-token-two",
            {"name": "platform-beta"},
        ),
    ]
    require(len(log) == len(expected), f"expected {len(expected)} requests, got {len(log)}")

    for index, (actual, wanted) in enumerate(zip(log, expected), start=1):
        method, target, authorization, body = wanted
        require(actual["method"] == method, f"request {index}: method mismatch")
        require(actual["target"] == target, f"request {index}: target mismatch: {actual['target']!r}")
        require(actual["headers"].get("content-type") == "application/json", f"request {index}: content type mismatch")
        try:
            actual_body = json.loads(actual["body"])
        except json.JSONDecodeError as error:
            raise AssertionError(f"request {index}: body is not valid JSON: {actual['body']!r}") from error
        require(actual_body == body, f"request {index}: JSON body mismatch: {actual_body!r}")
        if authorization is not None:
            require(actual["headers"].get("authorization") == authorization, f"request {index}: authorization mismatch")

    beta_payload = json.loads(log[2]["body"])
    optional_fields = {
        "description",
        "administrators",
        "members",
        "viewers",
        "supervisors",
        "zoneAssignmentConfigurations",
        "constraints",
        "operationTimeout",
        "machineNamingTemplate",
        "sharedResources",
        "placementPolicy",
        "customProperties",
    }
    require(set(beta_payload).isdisjoint(optional_fields), "unset optional fields must be omitted")
    require(beta_payload == {"name": "platform-beta"}, "project id belongs only in the path")
    require(log[2]["body"] == log[4]["body"], "401 retry body must be byte-identical")
    require(log[2]["target"] == log[4]["target"], "401 retry target must be identical")


def verify_terminal_failures(pwsh: str, temp: Path) -> None:
    cases = [
        (400, 2, ["Bearer access-token-one"]),
        (401, 4, ["Bearer access-token-one", "Bearer access-token-two"]),
    ]
    for status, expected_request_count, expected_authorization in cases:
        request_log = temp / f"requests-{status}.jsonl"
        result_path = temp / f"result-{status}.json"
        server, state = start_mock(CONTRACT_PATH, request_log, forced_update_status=status)
        try:
            completed = invoke_fixture(pwsh, server, result_path)
        finally:
            server.shutdown()
            server.server_close()

        require(completed.returncode != 0, f"HTTP {status} update failure was suppressed")
        require(not result_path.exists(), f"HTTP {status} failure unexpectedly produced a result")
        log = read_log(request_log)
        require(
            len(log) == expected_request_count,
            f"HTTP {status}: expected {expected_request_count} requests, got {len(log)}",
        )
        require(log[0]["target"] == "/iaas/api/login?apiVersion=2021-07-15", f"HTTP {status}: login missing")
        patch_requests = [entry for entry in log if entry["method"] == "PATCH"]
        require(
            [entry["headers"].get("authorization") for entry in patch_requests] == expected_authorization,
            f"HTTP {status}: retry authorization sequence changed",
        )
        require(
            all(
                entry["target"] == "/iaas/api/projects/project-alpha?apiVersion=2021-07-15"
                for entry in patch_requests
            ),
            f"HTTP {status}: failed PATCH target changed",
        )
        require(
            len({entry["body"] for entry in patch_requests}) == 1,
            f"HTTP {status}: retry body changed",
        )
        require(
            state.snapshot()
            == {
                "project-alpha": {"id": "project-alpha", "name": "legacy-alpha"},
                "project beta/blue": {"id": "project beta/blue", "name": "legacy-beta"},
            },
            f"HTTP {status}: failed updates changed project state",
        )
        require(
            state.login_count == (2 if status == 401 else 1),
            f"HTTP {status}: incorrect token exchange count",
        )


def main() -> None:
    contract = verify_documentation_contract()
    pwsh = shutil.which("pwsh")
    require(pwsh is not None, "pwsh is required")

    with tempfile.TemporaryDirectory(prefix="vcfa-contract-") as temporary:
        temp = Path(temporary)
        request_log = temp / "requests.jsonl"
        result_path = temp / "result.json"
        server, state = start_mock(CONTRACT_PATH, request_log)
        expected_named = {(item["method"], item["path"]) for item in contract["operations"]}
        require(state.named_operations == expected_named, "mock routes are not pinned to the named contract operations")
        try:
            completed = invoke_fixture(pwsh, server, result_path)
        finally:
            server.shutdown()
            server.server_close()

        require(
            completed.returncode == 0,
            "PowerShell fixture failed:\n" + completed.stdout + completed.stderr,
        )
        require(result_path.exists(), "PowerShell fixture did not write its result")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        log = read_log(request_log)
        verify_wire(log)

        version = tuple(int(part) for part in result["prerequisiteVersion"].split(".")[:2])
        require(version >= (13, 4), "VCF PowerCLI prerequisite is older than the manifest minimum")
        require([item["id"] for item in result["updated"]] == ["project-alpha", "project beta/blue"], "result order changed")
        require(result["updated"][0]["operationTimeout"] == 0, "explicit zero was not preserved")
        require(result["updated"][0]["sharedResources"] is False, "explicit false was not preserved")
        require(set(result["updated"][1]) == {"id", "name"}, "unset beta optionals leaked into the response")
        require(
            state.snapshot()
            == {
                "project-alpha": {
                    "id": "project-alpha",
                    "name": "platform-alpha",
                    "description": "Managed by VCF 9",
                    "administrators": [{"email": "admin@foundation.example"}],
                    "members": [{"email": "member@foundation.example"}],
                    "viewers": [{"email": "viewer@foundation.example"}],
                    "supervisors": [{"email": "supervisor@foundation.example"}],
                    "zoneAssignmentConfigurations": [{"zoneId": "zone-foundation", "priority": 1}],
                    "constraints": {
                        "network": [{"expression": "environment:production", "mandatory": True}]
                    },
                    "operationTimeout": 0,
                    "machineNamingTemplate": "foundation-${####}",
                    "sharedResources": False,
                    "placementPolicy": "SPREAD",
                    "customProperties": {"costCenter": "foundation"},
                },
                "project beta/blue": {"id": "project beta/blue", "name": "platform-beta"},
            },
            "final project state proves that work was lost or replayed incorrectly",
        )
        require(state.login_count == 2, "the expired access token must be refreshed exactly when needed")

        verify_terminal_failures(pwsh, temp)

    print("PASS: exact VCF Automation wire contract and mid-run token refresh verified")


if __name__ == "__main__":
    main()
