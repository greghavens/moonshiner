#!/usr/bin/env python3
"""Protected deterministic acceptance test for the PowerShell module."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "contract.json"
OFFICIAL_SOURCES = ROOT / "docs" / "official_sources.json"
MOCK = ROOT / "tests" / "mock_vcfa.py"
MODULE = ROOT / "VcfAutomation.Integrations" / "VcfAutomation.Integrations.psm1"
MANIFEST = ROOT / "VcfAutomation.Integrations" / "VcfAutomation.Integrations.psd1"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


@contextmanager
def mock_server(directory: Path, scenario: str):
    log_path = directory / f"{scenario}.jsonl"
    process = subprocess.Popen(
        [
            sys.executable,
            str(MOCK),
            "--contract",
            str(CONTRACT),
            "--log",
            str(log_path),
            "--scenario",
            scenario,
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        check(process.stdout is not None, "mock stdout was not captured")
        ready_line = process.stdout.readline()
        if not ready_line:
            stderr = process.stderr.read() if process.stderr else ""
            raise AssertionError(f"mock failed to start: {stderr}")
        base_url = json.loads(ready_line)["baseUrl"]
        yield base_url, log_path
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def run_powershell(base_url: str, optional_mode: str = "unset") -> subprocess.CompletedProcess[str]:
    script = r"""
$ErrorActionPreference = 'Stop'
Import-Module $env:VCFA_MODULE_PATH -Force
$properties = [ordered]@{
    organization = 'platform-team'
    repository = 'catalog-config'
}
$arguments = @{
    Server = $env:VCFA_BASE_URL
    AccessToken = 'fixture-token'
    ApiVersion = '2021-07-15'
    Name = 'github-config'
    IntegrationType = 'GitHub'
    IntegrationProperties = $properties
    PollIntervalMilliseconds = 0
}
if ($env:VCFA_OPTIONAL_MODE -eq 'true') {
    $arguments.Description = 'GitHub configuration source'
    $arguments.ValidateOnly = $true
} elseif ($env:VCFA_OPTIONAL_MODE -eq 'false-empty') {
    $arguments.Description = ''
    $arguments.ValidateOnly = $false
}
$result = New-VcfAutomationIntegration @arguments
$result | ConvertTo-Json -Depth 10 -Compress
"""
    environment = os.environ.copy()
    environment["VCFA_BASE_URL"] = base_url
    environment["VCFA_MODULE_PATH"] = str(MODULE)
    environment["VCFA_OPTIONAL_MODE"] = optional_mode
    return subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def read_log(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def assert_protected_contract_shape() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    check(contract["sourceKind"] == "reference-documentation", "contract source kind changed")
    check(
        "rather than a published specification" in contract["sourceNotice"],
        "contract must state that it is reference-derived, not a published specification",
    )
    operations = {(item["method"], item["path"]) for item in contract["operations"]}
    check(
        operations
        == {
            ("POST", "/iaas/api/integrations"),
            ("GET", "/iaas/api/request-tracker/{id}"),
        },
        "contract operation set changed",
    )
    provenance = json.loads(OFFICIAL_SOURCES.read_text(encoding="utf-8"))
    sources = provenance["sources"]
    check(len(sources) == 3, "official source inventory changed")
    check(
        all(
            item["url"].startswith("https://developer.broadcom.com/")
            and item["operation"]
            and item["fetchedOn"] == "2026-08-13"
            for item in sources
        ),
        "each official source must record its Broadcom URL, operation, and fetch date",
    )
    manifest = MANIFEST.read_text(encoding="utf-8")
    check("VMware.Sdk.Vcf.SddcManager" in manifest, "PowerCLI prerequisite declaration is missing")
    # The trace harness may stage the project at the root of its sandbox home,
    # alongside its own PowerShell toolchain.  Restrict this repository policy
    # check to the deliverable directory so harness-owned DLLs cannot be
    # mistaken for vendored project dependencies.
    vendored = [
        path
        for path in MODULE.parent.rglob("*")
        if path.is_file()
        and (path.suffix.lower() in {".dll", ".nupkg"} or "VMware.Sdk.Vcf" in path.name)
    ]
    check(not vendored, f"PowerCLI artifacts must not be vendored: {vendored}")


def assert_success_wire(log: list[dict[str, object]]) -> None:
    check(len(log) == 3, f"expected one POST and two polls, got {len(log)} requests")
    post, first_poll, second_poll = log
    check(post["method"] == "POST", "first request must be POST")
    check(
        post["target"] == "/iaas/api/integrations?apiVersion=2021-07-15",
        f"unexpected create target: {post['target']}",
    )
    check(post["query"] == {"apiVersion": ["2021-07-15"]}, "unset validateOnly must be omitted")
    check(
        post["headers"].get("authorization") == "Bearer fixture-token",
        "POST Authorization header must use the Bearer wire shape",
    )
    check(post["headers"].get("accept") == "application/json", "POST must request JSON")
    check(
        str(post["headers"].get("content-type", "")).split(";", 1)[0] == "application/json",
        "POST Content-Type must be application/json",
    )
    expected_body = {
        "name": "github-config",
        "integrationType": "GitHub",
        "integrationProperties": {
            "organization": "platform-team",
            "repository": "catalog-config",
        },
    }
    check(post["body"] == expected_body, f"POST body does not have the exact required shape: {post['body']}")
    optional_fields = {
        "description",
        "privateKeyId",
        "privateKey",
        "associatedCloudAccountIds",
        "customProperties",
        "tags",
        "certificateInfo",
    }
    check(
        optional_fields.isdisjoint(post["body"].keys()),
        "unset optional body fields must be absent",
    )

    expected_poll_target = "/iaas/api/request-tracker/req-001?apiVersion=2021-07-15"
    for index, poll in enumerate((first_poll, second_poll), start=1):
        check(poll["method"] == "GET", f"poll {index} must use GET")
        check(poll["target"] == expected_poll_target, f"poll {index} has the wrong target")
        check(poll["body"] is None, f"poll {index} must not send a request body")
        check(
            poll["headers"].get("authorization") == "Bearer fixture-token",
            f"poll {index} must retain Bearer authentication",
        )
        check(poll["headers"].get("accept") == "application/json", f"poll {index} must request JSON")


def assert_supplied_optional_wire(log: list[dict[str, object]]) -> None:
    check(len(log) == 3, "optional scenario must still poll to FINISHED")
    post = log[0]
    check(
        post["path"] == "/iaas/api/integrations",
        f"supplied validateOnly changed the create path: {post['path']}",
    )
    check(
        post["query"] == {"apiVersion": ["2021-07-15"], "validateOnly": ["true"]},
        "supplied validateOnly must be serialized as lowercase boolean text",
    )
    check(
        post["body"].get("description") == "GitHub configuration source",
        "supplied Description must be included in the JSON body",
    )


def assert_false_and_empty_optional_wire(log: list[dict[str, object]]) -> None:
    check(len(log) == 3, "false/empty optional scenario must still poll to FINISHED")
    post = log[0]
    check(
        post["path"] == "/iaas/api/integrations",
        f"explicit false validateOnly changed the create path: {post['path']}",
    )
    check(
        post["query"] == {"apiVersion": ["2021-07-15"], "validateOnly": ["false"]},
        "explicit false validateOnly must be included as lowercase boolean text",
    )
    check(
        "description" in post["body"] and post["body"]["description"] == "",
        "an explicitly supplied empty Description must remain present",
    )


def main() -> None:
    assert_protected_contract_shape()
    with tempfile.TemporaryDirectory(prefix="vcfa-contract-") as temp_name:
        temp_dir = Path(temp_name)

        with mock_server(temp_dir, "success") as (base_url, log_path):
            try:
                urllib.request.urlopen(f"{base_url}/not-in-contract", timeout=2)
                raise AssertionError("mock served an operation not named by the contract")
            except urllib.error.HTTPError as error:
                check(error.code == 404, "unknown mock operation must return 404")

            result = run_powershell(base_url)
            check(
                result.returncode == 0,
                f"PowerShell success scenario failed\nstdout: {result.stdout}\nstderr: {result.stderr}",
            )
            tracker = json.loads(result.stdout.strip())
            check(tracker["id"] == "req-001", "returned the wrong request tracker")
            check(tracker["status"] == "FINISHED", "function returned before FINISHED")
            check(tracker["progress"] == 100, "function did not return the terminal tracker")
            assert_success_wire(read_log(log_path))

        with mock_server(temp_dir, "success") as (base_url, log_path):
            result = run_powershell(base_url, optional_mode="true")
            check(
                result.returncode == 0,
                f"PowerShell optional scenario failed\nstdout: {result.stdout}\nstderr: {result.stderr}",
            )
            assert_supplied_optional_wire(read_log(log_path))

        with mock_server(temp_dir, "success") as (base_url, log_path):
            result = run_powershell(base_url, optional_mode="false-empty")
            check(
                result.returncode == 0,
                f"PowerShell false/empty optional scenario failed\nstdout: {result.stdout}\nstderr: {result.stderr}",
            )
            assert_false_and_empty_optional_wire(read_log(log_path))

        with mock_server(temp_dir, "failure") as (base_url, log_path):
            result = run_powershell(base_url)
            check(result.returncode != 0, "FAILED tracker must cause a PowerShell error")
            combined = result.stdout + result.stderr
            check("req-001" in combined, "failure error must identify the request")
            check("Provisioning failed" in combined, "failure error must include the server message")
            failure_log = read_log(log_path)
            check(len(failure_log) == 3, "failure scenario must stop on the FAILED tracker")

        with mock_server(temp_dir, "unknown") as (base_url, log_path):
            result = run_powershell(base_url)
            check(result.returncode != 0, "an unrecognized tracker status must cause an error")
            unknown_log = read_log(log_path)
            check(len(unknown_log) == 2, "unknown status must stop polling immediately")

    print("PASS: VCF Automation integration contract and async polling verified")


if __name__ == "__main__":
    main()
