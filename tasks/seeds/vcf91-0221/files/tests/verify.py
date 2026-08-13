#!/usr/bin/env python3
"""Protected deterministic acceptance verifier for the VCF SDDC LCM module."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
MANIFEST = ROOT / "VcfSddcLcm" / "VcfSddcLcm.psd1"
MODULE = ROOT / "VcfSddcLcm" / "VcfSddcLcm.psm1"
COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
SPEC_PATH = "specifications/sddc-lcm/sddc-lcm-openapi.yaml"
TASK_IDS = tuple(
    f"7f134a9c-1cd8-4f34-9a38-4681b1086{suffix:03x}"
    for suffix in range(0x2FB, 0x306)
)
CASE_NAMES = (
    "happy",
    "optional",
    "accepted-succeeded",
    "failed",
    "canceled",
    "lowercase-status",
    "missing-status",
    "mismatched-id",
    "invalid-accepted-id",
    "empty-optional",
    "timeout",
)


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def verify_provenance() -> dict[str, Any]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    expected_ops = [
        ("setConfig", "POST", "/v1/config"),
        ("getTask", "GET", "/v1/tasks/{taskId}"),
    ]
    actual_ops = [
        (item.get("operationId"), item.get("method"), item.get("path"))
        for item in contract.get("operations", [])
    ]
    source_ops = [
        (item.get("operationId"), item.get("method"), item.get("path"))
        for item in sources.get("operations", [])
    ]
    require(actual_ops == expected_ops, "derived contract operation set changed")
    require(source_ops == expected_ops, "official source operation records changed")
    require(contract["source"]["commit"] == COMMIT, "contract commit is not pinned")
    require(contract["source"]["path"] == SPEC_PATH, "contract spec path changed")
    require(sources["repositoryCommitSha"] == COMMIT, "source commit is not pinned")
    require(sources["specificationPath"] == SPEC_PATH, "source spec path changed")
    require(contract["source"]["apiVersion"] == "9.1.0.0", "wrong VCF API version")
    require(contract["source"]["license"] == "Apache-2.0", "wrong source license")
    require(
        contract["security"]
        == {"name": "bearerToken", "type": "http", "scheme": "Bearer", "bearerFormat": "JWT"},
        "bearer security contract does not match the source specification",
    )
    require(
        contract["schemas"]["TaskStatus"]["enum"]
        == ["PENDING", "SCHEDULED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELED"],
        "task lifecycle states do not match the source specification",
    )
    return contract


def verify_no_vendored_sdk() -> None:
    forbidden_suffixes = {".dll", ".nupkg"}
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT).as_posix()
        if path.is_file() and path.suffix.lower() in forbidden_suffixes:
            raise VerificationError(f"vendored binary/package is forbidden: {relative}")
        if path.is_dir() and path.name.lower().startswith("vmware.sdk.vcf"):
            raise VerificationError(f"vendored VMware SDK directory is forbidden: {relative}")

    manifest_text = MANIFEST.read_text(encoding="utf-8")
    require(
        "ModuleName = 'VMware.Sdk.Vcf.SddcManager'" in manifest_text
        and "RequiredVersion = '13.5.0.25380678'" in manifest_text,
        "manifest must consume the environment-provided VMware.Sdk.Vcf.SddcManager module",
    )


def wait_for_port_file(path: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise VerificationError(f"mock exited early\nstdout: {stdout}\nstderr: {stderr}")
        if path.exists() and path.read_text(encoding="ascii").strip():
            return int(path.read_text(encoding="ascii"))
        time.sleep(0.02)
    raise VerificationError("mock did not publish a loopback port")


def powershell_script(port: int, output_path: Path) -> str:
    manifest = str(MANIFEST).replace("'", "''")
    output = str(output_path).replace("'", "''")
    return f"""
$ErrorActionPreference = 'Stop'
Import-Module '{manifest}' -Force
$loadedSdk = Get-Module -Name VMware.Sdk.Vcf.SddcManager |
    Where-Object Version -EQ ([version]'13.5.0.25380678') |
    Select-Object -First 1
if ($null -eq $loadedSdk) {{
    throw 'Importing the companion module did not load the required VMware SDK module.'
}}
function Invoke-Case {{
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [scriptblock] $Action
    )
    $watch = [Diagnostics.Stopwatch]::StartNew()
    try {{
        $value = & $Action
        [pscustomobject] @{{
            name = $Name
            outcome = 'returned'
            result = $value
            errorType = $null
            errorMessage = $null
            elapsedSeconds = $watch.Elapsed.TotalSeconds
        }}
    }}
    catch {{
        [pscustomobject] @{{
            name = $Name
            outcome = 'threw'
            result = $null
            errorType = $_.Exception.GetType().FullName
            errorMessage = $_.Exception.Message
            elapsedSeconds = $watch.Elapsed.TotalSeconds
        }}
    }}
}}
$managerSecret = ConvertTo-SecureString 'SddcPass!42' -AsPlainText -Force
$managerCredential = [pscredential]::new('administrator@vsphere.local', $managerSecret)
$accessToken = ConvertTo-SecureString 'loopback-access-token' -AsPlainText -Force
$common = @{{
    BaseUri = 'http://127.0.0.1:{port}/sddc-lcm'
    AccessToken = $accessToken
    SddcLcmFqdn = 'sddc-lcm01.example.test'
    SddcManagerFqdn = 'sddc-manager01.example.test'
    SddcManagerCredential = $managerCredential
    SddcManagerSslThumbprint = 'AA:11:22:33'
    FleetLcmFqdn = 'fleet-lcm01.example.test'
    FleetLcmSslThumbprint = 'BB:44:55:66'
    PollIntervalSeconds = 0
    TimeoutSeconds = 10
}}
$results = @()
$results += Invoke-Case -Name 'happy' -Action {{ Set-VcfSddcLcmConfiguration @common }}

$fleetSecret = ConvertTo-SecureString 'FleetPass!84' -AsPlainText -Force
$fleetCredential = [pscredential]::new('fleet-admin', $fleetSecret)
$fleetToken = ConvertTo-SecureString 'fleet-ops-token' -AsPlainText -Force
$optional = $common.Clone()
$optional['FleetLcmCredential'] = $fleetCredential
$optional['FleetOpsToken'] = $fleetToken
$results += Invoke-Case -Name 'optional' -Action {{ Set-VcfSddcLcmConfiguration @optional }}

$results += Invoke-Case -Name 'accepted-succeeded' -Action {{ Set-VcfSddcLcmConfiguration @common }}
$results += Invoke-Case -Name 'failed' -Action {{ Set-VcfSddcLcmConfiguration @common }}
$results += Invoke-Case -Name 'canceled' -Action {{ Set-VcfSddcLcmConfiguration @common }}
$results += Invoke-Case -Name 'lowercase-status' -Action {{ Set-VcfSddcLcmConfiguration @common }}
$results += Invoke-Case -Name 'missing-status' -Action {{ Set-VcfSddcLcmConfiguration @common }}
$results += Invoke-Case -Name 'mismatched-id' -Action {{ Set-VcfSddcLcmConfiguration @common }}
$results += Invoke-Case -Name 'invalid-accepted-id' -Action {{ Set-VcfSddcLcmConfiguration @common }}

$emptyOptional = $common.Clone()
$emptyOptional['FleetOpsToken'] = [securestring]::new()
$results += Invoke-Case -Name 'empty-optional' -Action {{ Set-VcfSddcLcmConfiguration @emptyOptional }}

$timeout = $common.Clone()
$timeout['PollIntervalSeconds'] = 2
$timeout['TimeoutSeconds'] = 1
$results += Invoke-Case -Name 'timeout' -Action {{ Set-VcfSddcLcmConfiguration @timeout }}

ConvertTo-Json -InputObject $results -Depth 20 -Compress |
    Set-Content -LiteralPath '{output}' -NoNewline
"""


def run_workflow() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pwsh = shutil.which("pwsh")
    require(pwsh is not None, "PowerShell (pwsh) is required")
    module_probe = subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "$module = Get-Module -ListAvailable -Name VMware.Sdk.Vcf.SddcManager | "
            "Where-Object Version -EQ ([version]'13.5.0.25380678') | Select-Object -First 1; "
            "if ($null -ne $module) { exit 0 } else { exit 9 }",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
    )
    require(
        module_probe.returncode == 0,
        "environment prerequisite VMware.Sdk.Vcf.SddcManager is not installed",
    )

    with tempfile.TemporaryDirectory(prefix="vcf-sddc-lcm-") as directory:
        temp = Path(directory)
        log_path = temp / "requests.ndjson"
        port_path = temp / "port"
        output_path = temp / "result.json"
        runner_path = temp / "run.ps1"
        server = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "tests" / "mock_sddc_lcm.py"),
                "--contract",
                str(CONTRACT),
                "--log",
                str(log_path),
                "--port-file",
                str(port_path),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            port = wait_for_port_file(port_path, server)
            runner_path.write_text(powershell_script(port, output_path), encoding="utf-8")
            environment = os.environ.copy()
            environment.update(
                {
                    "HTTP_PROXY": "",
                    "HTTPS_PROXY": "",
                    "ALL_PROXY": "",
                    "NO_PROXY": "127.0.0.1,localhost",
                }
            )
            completed = subprocess.run(
                [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(runner_path)],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                timeout=30,
            )
            require(
                completed.returncode == 0,
                "PowerShell workflow failed\n"
                f"stdout: {completed.stdout}\nstderr: {completed.stderr}",
            )
            require(output_path.exists(), "PowerShell workflow produced no result")
            results = json.loads(output_path.read_text(encoding="utf-8"))
        finally:
            server.terminate()
            try:
                server.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.communicate(timeout=5)

        entries = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        require(isinstance(results, list), "PowerShell workflow result is not a case array")
        return results, entries


def verify_outcomes(results: list[dict[str, Any]]) -> None:
    require(len(results) == len(CASE_NAMES), "PowerShell did not report every verification case")
    require(
        [item.get("name") for item in results] == list(CASE_NAMES),
        "PowerShell verification cases are missing or out of order",
    )
    by_name = {item["name"]: item for item in results}

    returned = ("happy", "optional", "accepted-succeeded", "empty-optional")
    for name in returned:
        item = by_name[name]
        require(item.get("outcome") == "returned", f"{name} should return successfully")
        result = item.get("result")
        require(isinstance(result, dict), f"{name} did not return a Task object")
        index = CASE_NAMES.index(name)
        require(result.get("id") == TASK_IDS[index], f"{name} returned the wrong task")
        require(result.get("status") == "SUCCEEDED", f"{name} returned before task success")

    rejected = (
        "failed",
        "canceled",
        "lowercase-status",
        "missing-status",
        "mismatched-id",
        "invalid-accepted-id",
        "timeout",
    )
    for name in rejected:
        require(by_name[name].get("outcome") == "threw", f"{name} must throw")


def verify_wire(entries: list[dict[str, Any]]) -> None:
    require(entries, "loopback mock received no requests")
    require(
        all(entry.get("workflow") is not None for entry in entries),
        "module contacted a route outside setConfig/getTask",
    )
    grouped: dict[int, list[dict[str, Any]]] = {index: [] for index in range(len(CASE_NAMES))}
    for entry in entries:
        workflow = entry.get("workflow")
        require(isinstance(workflow, int) and workflow in grouped, "mock logged an unknown workflow")
        grouped[workflow].append(entry)

    expected_counts = {0: 4, 1: 3, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2, 7: 2, 8: 1, 9: 2}
    for index, count in expected_counts.items():
        require(
            len(grouped[index]) == count,
            f"{CASE_NAMES[index]} expected {count} request(s), got {len(grouped[index])}",
        )
    require(
        len(grouped[10]) in (2, 3),
        f"timeout must submit and poll at least once without busy-polling; got {len(grouped[10])} requests",
    )

    expected_body = {
        "fqdn": "sddc-lcm01.example.test",
        "sddcManager": {
            "fqdn": "sddc-manager01.example.test",
            "username": "administrator@vsphere.local",
            "password": "SddcPass!42",
            "sslThumbprint": "AA:11:22:33",
        },
        "fleetLcm": {
            "fqdn": "fleet-lcm01.example.test",
            "sslThumbprint": "BB:44:55:66",
        },
    }

    for workflow, workflow_entries in grouped.items():
        submit = workflow_entries[0]
        case_name = CASE_NAMES[workflow]
        require(submit["method"] == "POST", f"{case_name} setConfig must use POST")
        require(submit["path"] == "/sddc-lcm/v1/config", f"{case_name} setConfig path is wrong")
        require(submit["query"] == "", f"{case_name} setConfig must not add a query string")
        require(
            submit["headers"].get("authorization") == "Bearer loopback-access-token",
            f"{case_name} bearer authorization wire value is wrong",
        )
        content_type = submit["headers"].get("content-type", "").lower()
        require(
            content_type.split(";", 1)[0].strip() == "application/json",
            f"{case_name} setConfig content type is wrong",
        )

        body = expected_body
        if workflow == 1:
            body = json.loads(json.dumps(expected_body))
            body["fleetLcm"].update(
                {
                    "username": "fleet-admin",
                    "password": "FleetPass!84",
                    "opsToken": "fleet-ops-token",
                }
            )
        require(submit["bodyJson"] == body, f"{case_name} ConfigSpec JSON wire shape is not exact")
        require("vspCluster" not in submit["bodyJson"], f"{case_name} must omit vspCluster")
        require("vcenter" not in submit["bodyJson"], f"{case_name} must omit vcenter")
        if workflow != 1:
            fleet = submit["bodyJson"]["fleetLcm"]
            for optional in ("username", "password", "opsToken"):
                require(optional not in fleet, f"{case_name} must omit Fleet LCM {optional}")

        if workflow == 8:
            require(len(workflow_entries) == 1, "an invalid accepted task id must not be polled")
            continue
        task_path = f"/sddc-lcm/v1/tasks/{TASK_IDS[workflow]}"
        for poll_number, poll in enumerate(workflow_entries[1:], start=1):
            require(poll["method"] == "GET", f"{case_name} poll {poll_number} must use GET")
            require(poll["path"] == task_path, f"{case_name} poll {poll_number} task path is wrong")
            require(poll["query"] == "", f"{case_name} poll {poll_number} added a query string")
            require(poll["bodyUtf8"] == "", f"{case_name} poll {poll_number} sent a body")
            require(
                poll["headers"].get("authorization") == "Bearer loopback-access-token",
                f"{case_name} poll {poll_number} bearer authorization is wrong",
            )


def main() -> int:
    try:
        verify_provenance()
        verify_no_vendored_sdk()
        results, entries = run_workflow()
        verify_outcomes(results)
        verify_wire(entries)
    except (VerificationError, OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: contract provenance, SDK dependency, exact wire shape, and async terminal handling verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
