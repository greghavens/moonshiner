#!/usr/bin/env python3
"""Protected acceptance verifier for the VCF NSX Policy PowerShell task."""

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
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / "mock" / "nsx_policy_mock.py"
MANIFEST_PATH = ROOT / "src" / "VcfNsxPolicy" / "VcfNsxPolicy.psd1"

PINNED_SHA = "c3f3b52c845dd967cabbc21680e893292077d5ba"
PINNED_SPEC_PATH = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"
EXPECTED_OPERATIONS = {
    "ListAllInfraSegments": ("GET", "/policy/api/v1/infra/segments"),
    "PatchInfraSegment": ("PATCH", "/policy/api/v1/infra/segments/{segment-id}"),
    "ReadIntentStatus": (
        "GET",
        "/policy/api/v1/infra/realized-state/status",
    ),
}


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_and_check_contract() -> dict[str, Any]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))

    source = contract.get("source", {})
    require(source.get("commitSha") == PINNED_SHA, "contract commit SHA changed")
    require(source.get("specPath") == PINNED_SPEC_PATH, "contract spec path changed")
    require(source.get("license") == "Apache-2.0", "contract license changed")
    require(
        source.get("repository") == "https://github.com/vmware/vcf-api-specs",
        "contract repository changed",
    )

    operations = contract.get("operations")
    require(isinstance(operations, list), "contract operations must be an array")
    by_id = {item.get("operationId"): item for item in operations}
    require(
        set(by_id) == set(EXPECTED_OPERATIONS),
        "contract must contain exactly the three selected operationIds",
    )
    for operation_id, (method, path) in EXPECTED_OPERATIONS.items():
        operation = by_id[operation_id]
        require(operation.get("method") == method, f"{operation_id} method changed")
        require(operation.get("path") == path, f"{operation_id} path changed")

    require(
        sources.get("repositoryCommitSha") == PINNED_SHA,
        "official source commit SHA changed",
    )
    require(
        sources.get("specPath") == PINNED_SPEC_PATH,
        "official source spec path changed",
    )
    require(sources.get("license") == "Apache-2.0", "official source license changed")
    source_operations = sources.get("operations")
    require(
        isinstance(source_operations, list)
        and {item.get("operationId") for item in source_operations}
        == set(EXPECTED_OPERATIONS),
        "official_sources.json must record every selected operationId",
    )
    for item in source_operations:
        require(
            item.get("repositoryCommitSha") == PINNED_SHA
            and item.get("specPath") == PINNED_SPEC_PATH,
            f"source provenance missing for {item.get('operationId')}",
        )

    return contract


def wait_for_port_file(port_file: Path, process: subprocess.Popen[str]) -> int:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise VerificationError(
                f"mock exited during startup ({process.returncode})\n{stdout}\n{stderr}"
            )
        if port_file.exists():
            text = port_file.read_text(encoding="ascii").strip()
            if text:
                return int(text)
        time.sleep(0.02)
    raise VerificationError("timed out waiting for loopback mock")


def powershell_program() -> str:
    return r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

Import-Module $env:VCF_NSX_MANIFEST -Force -ErrorAction Stop

$newCommand = Get-Command New-VcfNsxPolicyClient -ErrorAction Stop
$connectionType = $newCommand.Parameters['Connection'].ParameterType.FullName

$client = New-VcfNsxPolicyClient `
    -Server ([uri] $env:VCF_NSX_SERVER) `
    -AccessToken $env:VCF_NSX_ACCESS_TOKEN

$first = @(Get-VcfNsxPolicySegment -Client $client)
$second = @(Get-VcfNsxPolicySegment -Client $client)

$setResult = Set-VcfNsxPolicySegment `
    -Client $client `
    -SegmentId $env:VCF_NSX_SEGMENT_ID `
    -DisplayName $env:VCF_NSX_DISPLAY_NAME `
    -ConnectivityPath $env:VCF_NSX_CONNECTIVITY_PATH `
    -TransportZonePath $env:VCF_NSX_TRANSPORT_ZONE_PATH `
    -TimeoutSeconds 5 `
    -PollIntervalMilliseconds 10

$result = [ordered]@{
    connectionParameterType = $connectionType
    first = @($first | ForEach-Object {
        [ordered]@{ display_name = $_.display_name; id = $_.id }
    })
    second = @($second | ForEach-Object {
        [ordered]@{ display_name = $_.display_name; id = $_.id }
    })
    setResult = $setResult
}

$result |
    ConvertTo-Json -Depth 20 |
    Set-Content -LiteralPath $env:VCF_NSX_RESULT -Encoding utf8
"""


def run_powershell(port: int, result_path: Path, work: Path) -> None:
    pwsh = shutil.which("pwsh")
    require(pwsh is not None, "pwsh is required")

    operation_seed = PINNED_SHA[:12]
    env = os.environ.copy()
    env.update(
        {
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "POWERSHELL_UPDATECHECK": "Off",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "VCF_NSX_MANIFEST": str(MANIFEST_PATH),
            "VCF_NSX_SERVER": f"http://127.0.0.1:{port}",
            "VCF_NSX_ACCESS_TOKEN": "loopback-contract-token",
            "VCF_NSX_SEGMENT_ID": f"workload-{operation_seed}",
            "VCF_NSX_DISPLAY_NAME": f"Workload {operation_seed}",
            "VCF_NSX_CONNECTIVITY_PATH": f"/infra/tier-1s/tier1-{operation_seed}",
            "VCF_NSX_TRANSPORT_ZONE_PATH": (
                "/infra/sites/default/enforcement-points/default/"
                f"transport-zones/tz-{operation_seed}"
            ),
            "VCF_NSX_RESULT": str(result_path),
        }
    )

    script_path = work / "acceptance.ps1"
    script_path.write_text(powershell_program(), encoding="utf-8")
    completed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if completed.returncode != 0:
        raise VerificationError(
            "PowerShell acceptance program failed\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    require(result_path.exists(), "PowerShell did not write its result")


def check_powershell_result(result_path: Path) -> dict[str, str]:
    result = json.loads(result_path.read_text(encoding="utf-8-sig"))
    require(
        result.get("connectionParameterType")
        == "VMware.Sdk.OpenApi.Cmdlets.IServerConnection",
        "New-VcfNsxPolicyClient must type Connection as the VMware SDK interface",
    )

    first = result.get("first")
    second = result.get("second")
    require(
        isinstance(first, list) and isinstance(second, list),
        "collection calls must return arrays",
    )
    require(len(first) >= 3 and first == second, "collection output is not stable")

    expected = sorted(
        first,
        key=lambda item: (
            str(item["display_name"]).casefold(),
            str(item["id"]).casefold(),
        ),
    )
    require(
        first == expected,
        "Get-VcfNsxPolicySegment must sort locally by display_name then id",
    )

    set_result = result.get("setResult")
    require(isinstance(set_result, dict), "Set command returned no result object")
    require(set_result.get("Status") == "SUCCESS", "realization was not successful")
    require(
        isinstance(set_result.get("PollCount"), int)
        and set_result["PollCount"] >= 3,
        "PATCH was treated as complete without polling to terminal status",
    )
    require(
        set_result.get("IntentPath")
        == f"/infra/segments/{set_result.get('SegmentId')}",
        "returned intent path is inconsistent",
    )

    return {
        "segment_id": str(set_result["SegmentId"]),
        "intent_path": str(set_result["IntentPath"]),
    }


def check_request_log(log_path: Path, values: dict[str, str]) -> None:
    entries = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    require(entries, "mock request log is empty")
    require(
        all(entry.get("operationId") in EXPECTED_OPERATIONS for entry in entries),
        "client requested an operation outside docs/contract.json",
    )
    require(
        all(entry.get("authorizationScheme") == "Bearer" for entry in entries),
        "client did not send bearer authorization",
    )

    list_entries = [
        entry for entry in entries if entry["operationId"] == "ListAllInfraSegments"
    ]
    require(
        len(list_entries) >= 4,
        "client did not follow cursor pagination for both collection calls",
    )
    require(
        {entry.get("responseOrder") for entry in list_entries}
        == {"canonical", "reversed"},
        "mock did not flip collection element order on every response",
    )
    first_page_entries = [
        entry for entry in list_entries if "cursor" not in entry.get("query", {})
    ]
    later_page_entries = [
        entry for entry in list_entries if "cursor" in entry.get("query", {})
    ]
    require(
        len(first_page_entries) >= 2 and len(later_page_entries) >= 2,
        "collection cursor was not consumed",
    )

    patch_entries = [
        entry for entry in entries if entry["operationId"] == "PatchInfraSegment"
    ]
    require(len(patch_entries) == 1, "expected one segment PATCH")
    patch = patch_entries[0]
    require(
        patch["path"].endswith("/" + values["segment_id"]),
        "PATCH segment path is incorrect",
    )
    require(
        (patch.get("contentType") or "").split(";", 1)[0].lower()
        == "application/json",
        "PATCH content type must be application/json",
    )
    body = patch.get("body")
    require(
        isinstance(body, dict)
        and body.get("resource_type") == "Segment"
        and body.get("id") == values["segment_id"]
        and isinstance(body.get("display_name"), str)
        and body["display_name"],
        "PATCH body does not satisfy the focused Segment contract",
    )

    status_entries = [
        entry for entry in entries if entry["operationId"] == "ReadIntentStatus"
    ]
    require(len(status_entries) >= 3, "client did not poll through intermediate states")
    require(
        min(entry["sequence"] for entry in status_entries) > patch["sequence"],
        "status polling must start after PATCH",
    )
    require(
        all(
            entry.get("query", {}).get("intent_path") == [values["intent_path"]]
            for entry in status_entries
        ),
        "status polling used the wrong intent_path",
    )
    require(
        max(entry.get("pollCount", 0) for entry in status_entries) >= 3,
        "terminal status was not observed",
    )


def verify() -> None:
    load_and_check_contract()
    with tempfile.TemporaryDirectory(prefix="vcf91-0057-") as temporary:
        work = Path(temporary)
        request_log = work / "requests.jsonl"
        port_file = work / "port"
        result_path = work / "result.json"

        process = subprocess.Popen(
            [
                sys.executable,
                str(MOCK_PATH),
                "--contract",
                str(CONTRACT_PATH),
                "--request-log",
                str(request_log),
                "--port-file",
                str(port_file),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            port = wait_for_port_file(port_file, process)
            run_powershell(port, result_path, work)
            values = check_powershell_result(result_path)
            check_request_log(request_log, values)
        finally:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)


def main() -> int:
    try:
        verify()
    except (VerificationError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: VCF NSX Policy contract, sorting, and realization polling verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
