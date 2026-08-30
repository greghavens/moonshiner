#!/usr/bin/env python3
"""Deterministic acceptance check for the VCF Logs PowerShell integration."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
MANIFEST = ROOT / "src" / "Vcf.OperationsForLogs.psd1"
MODULE = ROOT / "src" / "Vcf.OperationsForLogs.psm1"
MOCK = ROOT / "mock" / "vcf_logs_mock.py"
EXERCISE = ROOT / "tests" / "invoke_upgrade.ps1"

EXPECTED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC = "specifications/vcf-operations/vcf-operations-for-logs-openapi.json"
EXPECTED_OPERATIONS = {
    "POST_upgrades": ("POST", "/upgrades"),
    "PUT_upgrades-version-eula": ("PUT", "/upgrades/{version}/eula"),
    "GET_upgrades-version": ("GET", "/upgrades/{version}"),
}


def fail(message: str) -> None:
    raise AssertionError(message)


def verify_contract() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    if sources.get("tag") != "9.0.0.0":
        fail("official_sources.json must pin tag 9.0.0.0")
    if sources.get("commit_sha") != EXPECTED_SHA:
        fail("official_sources.json has the wrong tag commit SHA")
    if sources.get("spec_path") != EXPECTED_SPEC:
        fail("official_sources.json has the wrong specification path")
    if sources.get("license") != "Apache-2.0":
        fail("official_sources.json must record the Apache-2.0 source license")

    source_operations = {
        item["operationId"]: (item["method"], item["path"])
        for item in sources.get("operations", [])
    }
    if source_operations != EXPECTED_OPERATIONS:
        fail("official_sources.json must list every selected operationId exactly once")

    found = {}
    for path, path_item in contract.get("paths", {}).items():
        for method, operation in path_item.items():
            if isinstance(operation, dict) and "operationId" in operation:
                found[operation["operationId"]] = (method.upper(), path)
    if found != EXPECTED_OPERATIONS:
        fail("contract.json must contain exactly the three pinned upgrade operations")
    if contract.get("info") != {"title": "VCF Operations for Logs", "version": "v2"}:
        fail("contract.json is not the VCF Operations for Logs v2 contract")
    if contract.get("servers") != [{"url": "/api/v2"}]:
        fail("contract.json must preserve the /api/v2 server prefix")

    schemas = contract["components"]["schemas"]
    if schemas["upgrades.post.request"].get("required") != ["pakUrl"]:
        fail("the start-upgrade request contract must require pakUrl")
    if schemas["upgrades.version.eula.put.request"].get("required") != ["accepted"]:
        fail("the EULA request contract must require accepted")
    expected_states = [
        "Started",
        "PendingSnapshot",
        "CreatingSnapshot",
        "Pending",
        "TransferringPak",
        "Upgrading",
        "Restarting",
        "Verifying",
        "Complete",
        "Cancelled",
        "Failed",
        "NewDeployment",
    ]
    if schemas["upgrade.state"].get("enum") != expected_states:
        fail("the 9.0 upgrade status enum was changed")


def verify_manifest() -> None:
    if shutil.which("pwsh") is None:
        fail("pwsh is required")
    script = (
        "$m=Import-PowerShellDataFile -Path $env:VCF_LOGS_MANIFEST;"
        "$r=$m.RequiredModules[0];"
        "[pscustomobject]@{Name=$r.ModuleName;Version=$r.RequiredVersion.ToString();"
        "RootModule=$m.RootModule;Exports=@($m.FunctionsToExport)}|ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "VCF_LOGS_MANIFEST": str(MANIFEST)},
    )
    if result.returncode != 0:
        fail(f"module manifest is not valid: {result.stderr.strip()}")
    details = json.loads(result.stdout.strip())
    if details["Name"] != "VMware.Sdk.Vcf.Ops" or details["Version"] != "13.4.0.24798382":
        fail("manifest must use the environment-provided VCF 9.0 VMware.Sdk.Vcf.Ops module")
    if details["RootModule"] != MODULE.name:
        fail("manifest must load Vcf.OperationsForLogs.psm1")
    if details["Exports"] != ["Invoke-VcfLogsUpgrade"]:
        fail("manifest must export only Invoke-VcfLogsUpgrade")

    vendored = [
        path
        for path in ROOT.rglob("*")
        if path != MANIFEST and "VMware.Sdk.Vcf" in path.name
    ]
    if vendored:
        fail("VMware.Sdk.Vcf modules must not be vendored")


def verify_interface() -> None:
    script = (
        "Import-Module $env:VCF_LOGS_MODULE -Force;"
        "$command=Get-Command Invoke-VcfLogsUpgrade;"
        "$names=@('ServerUri','SessionId','PakUrl','PollIntervalMilliseconds','SkipCertificateCheck');"
        "$result=[ordered]@{};"
        "foreach($name in $names){"
        "$parameter=$command.Parameters[$name];"
        "$mandatory=@($parameter.Attributes|Where-Object {$_ -is [System.Management.Automation.ParameterAttribute]}|"
        "ForEach-Object {$_.Mandatory}) -contains $true;"
        "$result[$name]=[ordered]@{Type=$parameter.ParameterType.FullName;Mandatory=$mandatory}};"
        "$result|ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "VCF_LOGS_MODULE": str(MODULE)},
    )
    if result.returncode != 0:
        fail(f"root module cannot be imported: {result.stderr.strip()}")
    details = json.loads(result.stdout.strip())
    expected = {
        "ServerUri": {"Type": "System.Uri", "Mandatory": True},
        "SessionId": {"Type": "System.String", "Mandatory": True},
        "PakUrl": {"Type": "System.Uri", "Mandatory": True},
        "PollIntervalMilliseconds": {"Type": "System.Int32", "Mandatory": False},
        "SkipCertificateCheck": {
            "Type": "System.Management.Automation.SwitchParameter",
            "Mandatory": False,
        },
    }
    if details != expected:
        fail(f"Invoke-VcfLogsUpgrade does not have the supplied signature: {details!r}")


def read_ready_file(path: Path, process: subprocess.Popen[str]) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            fail(f"mock exited before readiness: {stdout}\n{stderr}")
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.02)
    fail("mock did not become ready")


def run_scenario(
    version: str,
    terminal_state: str,
    nonterminal_polls: int,
    poll_interval_ms: int,
) -> tuple[list[dict], str, str]:
    session_id = "session-vcf90-0081"
    with tempfile.TemporaryDirectory(prefix="vcf-logs-") as temporary:
        temp = Path(temporary)
        request_log = temp / "requests.jsonl"
        ready_file = temp / "ready.json"
        process = subprocess.Popen(
            [
                sys.executable,
                str(MOCK),
                "--contract",
                str(CONTRACT),
                "--request-log",
                str(request_log),
                "--ready-file",
                str(ready_file),
                "--version",
                version,
                "--terminal-state",
                terminal_state,
                "--nonterminal-polls",
                str(nonterminal_polls),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            ready = read_ready_file(ready_file, process)
            pak_url = ready["baseUri"] + "/fixtures/vcf-operations-for-logs-9.0.1.pak"
            result = subprocess.run(
                [
                    "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-File",
                    str(EXERCISE),
                    "-ModulePath",
                    str(MODULE),
                    "-ServerUri",
                    ready["baseUri"],
                    "-SessionId",
                    session_id,
                    "-PakUrl",
                    pak_url,
                    "-PollIntervalMilliseconds",
                    str(poll_interval_ms),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
                env={**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"},
            )
            if result.returncode != 0:
                fail(f"PowerShell scenario failed:\n{result.stdout}\n{result.stderr}")
            output_lines = [line for line in result.stdout.splitlines() if line.strip()]
            if not output_lines:
                fail("PowerShell scenario produced no result")
            output = json.loads(output_lines[-1])
            expected_output = {
                "clusterStatus": terminal_state,
                "version": version,
                "eulaAccepted": True,
            }
            if output != expected_output:
                fail(f"unexpected final status: {output!r}")
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

        requests = [json.loads(line) for line in request_log.read_text(encoding="utf-8").splitlines()]

    return requests, pak_url, session_id


def verify_scenario(
    version: str,
    terminal_state: str,
    nonterminal_polls: int,
    poll_interval_ms: int,
) -> None:
    requests, pak_url, session_id = run_scenario(
        version,
        terminal_state,
        nonterminal_polls,
        poll_interval_ms,
    )
    get_count = nonterminal_polls + 1
    expected_methods = ["POST", "PUT"] + ["GET"] * get_count
    if [item["method"] for item in requests] != expected_methods:
        fail("upgrade was not started, accepted, and polled exactly to its terminal state")
    expected_operations = ["POST_upgrades", "PUT_upgrades-version-eula"] + [
        "GET_upgrades-version"
    ] * get_count
    operation_ids = [item["operationId"] for item in requests]
    if operation_ids != expected_operations:
        fail(f"unexpected operation sequence: {operation_ids!r}")

    version_path = f"/api/v2/upgrades/{version}"
    expected_paths = [
        "/api/v2/upgrades",
        version_path + "/eula",
    ] + [version_path] * get_count
    if [item["path"] for item in requests] != expected_paths:
        fail("request paths do not match the 9.0 contract")
    if any(item["query"] != "" for item in requests):
        fail("unset query options must be omitted, not sent empty")

    start_body = json.loads(requests[0]["body"])
    eula_body = json.loads(requests[1]["body"])
    if start_body != {"pakUrl": pak_url}:
        fail(f"start request has the wrong JSON wire shape: {start_body!r}")
    if eula_body != {"accepted": True}:
        fail(f"EULA request has the wrong JSON wire shape: {eula_body!r}")
    if any(item["body"] != "" for item in requests[2:]):
        fail("polling GET requests must omit the request body")

    for index, item in enumerate(requests):
        headers = item["headers"]
        if headers.get("authorization") != f"Bearer {session_id}":
            fail(f"request {index + 1} did not carry the bearer session ID")
        if headers.get("accept") != "application/json":
            fail(f"request {index + 1} did not request JSON")
    for item in requests[:2]:
        if not item["headers"].get("content-type", "").lower().startswith("application/json"):
            fail("JSON writes must use application/json")
    for item in requests[2:]:
        if "content-type" in item["headers"]:
            fail("polling GET requests must omit Content-Type")
        if int(item["headers"].get("content-length", "0")) != 0:
            fail("polling GET requests must omit an empty payload")

    if poll_interval_ms:
        poll_times = [item["receivedAtNs"] for item in requests[2:]]
        # Use a wide tolerance for scheduler granularity while still separating
        # a real delay from immediate loopback requests.
        minimum_gap_ns = poll_interval_ms * 1_000_000 // 2
        gaps = [later - earlier for earlier, later in zip(poll_times, poll_times[1:])]
        if any(gap < minimum_gap_ns for gap in gaps):
            fail("PollIntervalMilliseconds was not observed between nonterminal polls")


def verify_wire() -> None:
    # A long nonterminal run discriminates implementations with an arbitrary
    # retry ceiling while keeping the check fast on the loopback service.
    verify_scenario(
        "9.0.1.0.25000000",
        "Complete",
        nonterminal_polls=128,
        poll_interval_ms=0,
    )
    verify_scenario(
        "9.0.2.0.25000001",
        "Cancelled",
        nonterminal_polls=2,
        poll_interval_ms=200,
    )
    verify_scenario(
        "9.0.3.0.25000002",
        "Failed",
        nonterminal_polls=1,
        poll_interval_ms=0,
    )


def main() -> None:
    verify_contract()
    verify_manifest()
    verify_interface()
    verify_wire()
    print("PASS: VCF Operations for Logs 9.0 upgrade integration")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
