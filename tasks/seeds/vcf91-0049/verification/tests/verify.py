#!/usr/bin/env python3
"""Protected deterministic acceptance verifier for vcf91-0049."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MODULE_DIR = ROOT / "src" / "Vcf.Nsx.Policy.Async"
MANIFEST_PATH = MODULE_DIR / "Vcf.Nsx.Policy.Async.psd1"
MODULE_PATH = MODULE_DIR / "Vcf.Nsx.Policy.Async.psm1"
MOCK_PATH = ROOT / "fixtures" / "nsx_policy_mock.py"
PINNED_SHA = "c3f3b52c845dd967cabbc21680e893292077d5ba"
PINNED_SPEC_PATH = "specifications/nsx/openapi-2.0/nsx_policy_api.yaml"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run(
    args: list[str],
    *,
    timeout: float = 20,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        env=env,
    )


def load_contract() -> tuple[dict[str, Any], dict[str, Any]]:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))

    require(contract["format"] == "openapi-2.0-reduced-contract", "contract format changed")
    require(contract["basePath"] == "/policy/api/v1", "wrong NSX Policy base path")
    require(contract["derived_from"]["commit_sha"] == PINNED_SHA, "contract is not pinned to VCF 9.1 commit")
    require(contract["derived_from"]["spec_path"] == PINNED_SPEC_PATH, "wrong contract source path")
    require(contract["derived_from"]["license"] == "Apache-2.0", "source license must be Apache-2.0")

    require(sources["commit_sha"] == PINNED_SHA, "official source commit changed")
    require(sources["spec_path"] == PINNED_SPEC_PATH, "official source path changed")
    require(sources["license"] == "Apache-2.0", "official source license changed")
    require(PINNED_SHA in sources["spec_url"], "spec URL is not commit-pinned")
    require(PINNED_SPEC_PATH in sources["spec_url"], "spec URL does not name the specification")

    operations = contract["operations"]
    source_operations = sources["operations"]
    require(len(operations) == 2, "the reduced contract must contain exactly two operations")
    require(
        [(item["operationId"], item["method"], item["path"]) for item in operations]
        == [(item["operationId"], item["method"], item["path"]) for item in source_operations],
        "official source operation records do not match the contract",
    )
    require(
        [item["operationId"] for item in operations]
        == ["PatchInfraSegment", "ReadIntentStatus"],
        "unexpected operationIds",
    )

    patch, status = operations
    require(patch["method"] == "PATCH", "segment operation must be PATCH")
    require(patch["path"] == "/infra/segments/{segment-id}", "segment operation path changed")
    require(patch["request"]["content_type"] == "application/json", "segment content type changed")
    require(patch["responses"]["200"]["body"] is None, "PATCH 200 response must have no body")

    require(status["method"] == "GET", "status operation must be GET")
    require(status["path"] == "/infra/realized-state/status", "status path changed")
    require(status["query_parameters"]["intent_path"]["required"] is True, "intent_path must be required")
    require(status["query_parameters"]["include_enforced_status"]["required"] is False, "include_enforced_status must stay optional")
    require(status["query_parameters"]["site_path"]["required"] is False, "site_path must stay optional")
    states = status["responses"]["200"]["properties"]["consolidated_status"]["properties"]["consolidated_status"]["enum"]
    require(
        states
        == [
            "SUCCESS",
            "IN_PROGRESS",
            "ERROR",
            "UNKNOWN",
            "UNINITIALIZED",
            "SANDBOXED_REALIZATION_PENDING",
        ],
        "ConfigState extraction changed",
    )
    return contract, sources


def inspect_powershell() -> None:
    require(MANIFEST_PATH.is_file(), "module manifest is missing")
    require(MODULE_PATH.is_file(), "module implementation is missing")

    ps_inspector = r'''
param([string] $ManifestPath, [string] $ModulePath)
$ErrorActionPreference = 'Stop'
$manifest = Import-PowerShellDataFile -Path $ManifestPath
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $ModulePath,
    [ref] $tokens,
    [ref] $errors
)
$functions = @{}
foreach ($name in @('New-VcfNsxPowerCliTransport', 'Set-VcfNsxInfraSegment')) {
    $definition = $ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq $name
    }, $true) | Select-Object -First 1
    if ($null -eq $definition) {
        $functions[$name] = $null
        continue
    }
    $functions[$name] = @(
        $definition.Body.FindAll({
            param($node)
            $node -is [System.Management.Automation.Language.CommandAst]
        }, $true) | ForEach-Object { $_.GetCommandName() } | Where-Object { $null -ne $_ }
    )
}
$required = @()
if ($manifest.ContainsKey('RequiredModules')) {
    foreach ($item in @($manifest.RequiredModules)) {
        if ($item -is [string]) {
            $required += [ordered]@{ name = $item; version = $null }
        } else {
            $required += [ordered]@{
                name = [string] $item.ModuleName
                version = [string] $item.ModuleVersion
            }
        }
    }
}
[ordered]@{
    parseErrors = @($errors | ForEach-Object { $_.Message })
    requiredModules = $required
    functions = $functions
} | ConvertTo-Json -Depth 20 -Compress
'''

    with tempfile.TemporaryDirectory(prefix="vcf91-0049-ast-") as temp_dir:
        script_path = Path(temp_dir) / "inspect.ps1"
        script_path.write_text(ps_inspector, encoding="utf-8", newline="\n")
        result = run(
            [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(script_path),
                str(MANIFEST_PATH),
                str(MODULE_PATH),
            ]
        )
    require(result.returncode == 0, f"PowerShell inspection failed: {result.stderr.strip()}")
    inspection = json.loads(result.stdout.strip())
    require(not inspection["parseErrors"], f"PowerShell parse errors: {inspection['parseErrors']}")

    dependencies = {item["name"]: item["version"] for item in inspection["requiredModules"]}
    require("VMware.Sdk.Nsx.Policy" in dependencies, "manifest must require VMware.Sdk.Nsx.Policy")
    version = dependencies["VMware.Sdk.Nsx.Policy"]
    require(version and tuple(int(part) for part in re.findall(r"\d+", version)[:2]) >= (13, 5), "VCF 9.1 SDK dependency must be 13.5 or later")

    transport_commands = set(inspection["functions"]["New-VcfNsxPowerCliTransport"] or [])
    require(
        {
            "Initialize-SegmentSubnet",
            "Initialize-Segment",
            "Invoke-PatchInfraSegment",
            "Invoke-ReadIntentStatus",
        }.issubset(transport_commands),
        "production transport must invoke the VCF PowerCLI NSX Policy initializer and operation commands",
    )
    workflow_commands = set(inspection["functions"]["Set-VcfNsxInfraSegment"] or [])
    require(
        "New-VcfNsxPowerCliTransport" in workflow_commands,
        "workflow must create the PowerCLI transport when no transport is supplied",
    )
    all_commands = transport_commands | workflow_commands
    require(
        not ({"Invoke-WebRequest", "Invoke-RestMethod"} & all_commands),
        "production module must use VCF PowerCLI rather than an independent REST client",
    )

    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT).as_posix().lower()
        require(path.suffix.lower() not in {".dll", ".nupkg"}, f"vendored binary/package is forbidden: {relative}")
        require(not relative.startswith("vmware.sdk."), f"vendored VMware module is forbidden: {relative}")


def wait_for_ready(path: Path, process: subprocess.Popen[str]) -> str:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size:
            return path.read_text(encoding="utf-8").strip()
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(f"mock exited before ready: {stdout}\n{stderr}")
        time.sleep(0.02)
    raise AssertionError("loopback mock did not become ready")


def scenario_values() -> dict[str, str]:
    digest = hashlib.sha256(b"vcf91-0049-wire-scenario").digest()
    suffix = digest.hex()[:8]
    return {
        "segment_id": f"seg-{suffix} blue-team",
        "display_name": f"payments-{suffix}-café",
        "gateway_address": f"10.{digest[4]}.{digest[5]}.1/24",
        "connectivity_path": f"/infra/tier-1s/t1-{digest.hex()[12:20]}",
    }


def exercise_scripted_transport() -> None:
    runner = r'''
param([string] $ModulePath)
$ErrorActionPreference = 'Stop'
Import-Module $ModulePath -Force -WarningAction SilentlyContinue

function New-StateTransport {
    param([object[]] $States)

    $cursor = [pscustomobject] @{ Value = 0 }
    return {
        param($Request)
        if ([string] $Request.OperationId -eq 'PatchInfraSegment') {
            return $null
        }
        $position = [math]::Min($cursor.Value, $States.Count - 1)
        $state = [string] $States[$position]
        $cursor.Value++
        if ($state -eq '<missing>') {
            return [pscustomobject] @{ consolidated_status = [pscustomobject] @{} }
        }
        return [pscustomobject] @{
            consolidated_status = [pscustomobject] @{ consolidated_status = $state }
        }
    }.GetNewClosure()
}

function Invoke-Workflow {
    param(
        [scriptblock] $CaseTransport,
        [int] $PollIntervalMilliseconds = 0,
        [int] $TimeoutSeconds = 5
    )
    Set-VcfNsxInfraSegment `
        -SegmentId 'seg/with space' `
        -DisplayName 'segment' `
        -GatewayAddress '10.0.0.1/24' `
        -ConnectivityPath '/infra/tier-1s/t1' `
        -PollIntervalMilliseconds $PollIntervalMilliseconds `
        -TimeoutSeconds $TimeoutSeconds `
        -Transport $CaseTransport
}

function Get-FailureMessage {
    param([scriptblock] $Action)
    try {
        $null = & $Action
        return $null
    } catch {
        return [string] $_.Exception.Message
    }
}

$requests = [Collections.Generic.List[object]]::new()
$presenceTransport = {
    param($Request)
    $requests.Add($Request)
    if ([string] $Request.OperationId -eq 'ReadIntentStatus') {
        return [pscustomobject] @{
            consolidated_status = [pscustomobject] @{ consolidated_status = 'SUCCESS' }
        }
    }
    return $null
}.GetNewClosure()
$presenceResult = Set-VcfNsxInfraSegment `
    -SegmentId 'seg/with space' `
    -DisplayName 'segment' `
    -GatewayAddress '10.0.0.1/24' `
    -ConnectivityPath '/infra/tier-1s/t1' `
    -Description 'description' `
    -TransportZonePath '/infra/sites/default/enforcement-points/default/transport-zones/tz' `
    -AdminState DOWN `
    -OverlayId 0 `
    -VlanIds @('100', '200') `
    -PollIntervalMilliseconds 0 `
    -TimeoutSeconds 5 `
    -Transport $presenceTransport

$allStatesResult = Invoke-Workflow -CaseTransport (
    New-StateTransport -States @(
        'IN_PROGRESS',
        'UNKNOWN',
        'UNINITIALIZED',
        'SANDBOXED_REALIZATION_PENDING',
        'SUCCESS'
    )
)
$errorMessage = Get-FailureMessage {
    Invoke-Workflow -CaseTransport (New-StateTransport -States @('ERROR'))
}
$missingMessage = Get-FailureMessage {
    Invoke-Workflow -CaseTransport (New-StateTransport -States @('<missing>'))
}
$unsupportedMessage = Get-FailureMessage {
    Invoke-Workflow -CaseTransport (New-StateTransport -States @('UNSUPPORTED'))
}
$timeoutWatch = [Diagnostics.Stopwatch]::StartNew()
$timeoutMessage = Get-FailureMessage {
    Invoke-Workflow `
        -CaseTransport (New-StateTransport -States @('IN_PROGRESS')) `
        -PollIntervalMilliseconds 60000 `
        -TimeoutSeconds 1
}
$timeoutWatch.Stop()

[ordered] @{
    presence = [ordered] @{
        result = $presenceResult
        requests = @($requests)
    }
    allStates = $allStatesResult
    errorMessage = $errorMessage
    missingMessage = $missingMessage
    unsupportedMessage = $unsupportedMessage
    timeoutMessage = $timeoutMessage
    timeoutElapsedSeconds = $timeoutWatch.Elapsed.TotalSeconds
} | ConvertTo-Json -Depth 30 -Compress
'''

    with tempfile.TemporaryDirectory(prefix="vcf91-0049-behavior-") as temp_dir:
        script_path = Path(temp_dir) / "behavior.ps1"
        script_path.write_text(runner, encoding="utf-8", newline="\n")
        result = run(
            [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(script_path),
                str(MODULE_PATH),
            ],
            timeout=12,
        )

    require(result.returncode == 0, f"scripted transport checks failed: {result.stderr.strip()}")
    stdout_lines = [line for line in result.stdout.splitlines() if line.strip()]
    require(len(stdout_lines) == 1, f"scripted checks emitted unexpected output: {stdout_lines}")
    report = json.loads(stdout_lines[0])

    presence = report["presence"]
    require(presence["result"]["Status"] == "SUCCESS", "presence case did not succeed")
    require(presence["result"]["PollCount"] == 1, "presence case poll count differs")
    requests = presence["requests"]
    require(len(requests) == 2, "presence case must issue one PATCH and one GET")
    patch_request, status_request = requests
    require(patch_request["OperationId"] == "PatchInfraSegment", "presence PATCH operationId differs")
    require(patch_request["Method"] == "PATCH", "presence PATCH method differs")
    require(patch_request["Path"] == "/infra/segments/seg%2Fwith%20space", "segment path was not escaped")
    require(patch_request["PathParameters"] == {"segment-id": "seg/with space"}, "raw path parameter differs")
    require(patch_request["Query"] == {}, "PATCH query must remain empty")
    require(
        patch_request["Body"]
        == {
            "display_name": "segment",
            "connectivity_path": "/infra/tier-1s/t1",
            "subnets": [{"gateway_address": "10.0.0.1/24"}],
            "description": "description",
            "transport_zone_path": "/infra/sites/default/enforcement-points/default/transport-zones/tz",
            "admin_state": "DOWN",
            "overlay_id": 0,
            "vlan_ids": ["100", "200"],
        },
        "bound optional Segment fields were not preserved exactly",
    )
    require(status_request["OperationId"] == "ReadIntentStatus", "presence status operationId differs")
    require(status_request["Method"] == "GET", "presence status method differs")
    require(status_request["Path"] == "/infra/realized-state/status", "presence status path differs")
    require(status_request["PathParameters"] == {}, "status path parameters must be empty")
    require(status_request["Query"] == {"intent_path": "/infra/segments/seg/with space"}, "status query differs")
    require(status_request["Body"] is None, "status request body must be null at the transport seam")

    require(report["allStates"]["Status"] == "SUCCESS", "a declared nonterminal state was rejected")
    require(report["allStates"]["PollCount"] == 5, "not every declared nonterminal state was polled")
    require("realization failed" in report["errorMessage"], "ERROR did not throw a realization failure")
    require("no consolidated status" in report["missingMessage"], "missing status was not rejected")
    require("unsupported ConfigState" in report["unsupportedMessage"], "unsupported status was not rejected")
    require("Timed out" in report["timeoutMessage"], "nonterminal polling did not time out")
    require(report["timeoutElapsedSeconds"] < 6, "timeout did not bound the nonterminal poll interval")


def exercise_powercli_transport() -> None:
    runner = r'''
param([string] $ModulePath)
$ErrorActionPreference = 'Stop'
$global:SdkCalls = [Collections.Generic.List[object]]::new()

function global:Initialize-SegmentSubnet {
    [CmdletBinding()]
    param([string] $GatewayAddress, [string[]] $DhcpRanges)
    $bound = [ordered] @{}
    foreach ($item in $PSBoundParameters.GetEnumerator()) { $bound[$item.Key] = $item.Value }
    $global:SdkCalls.Add([pscustomobject] @{ Command = 'Initialize-SegmentSubnet'; Bound = $bound })
    return [pscustomobject] $bound
}

function global:Initialize-Segment {
    [CmdletBinding()]
    param(
        [string] $DisplayName,
        [string] $ConnectivityPath,
        [object[]] $Subnets,
        [string] $Description,
        [string] $TransportZonePath,
        [string] $AdminState,
        [int] $OverlayId,
        [string[]] $VlanIds
    )
    $bound = [ordered] @{}
    foreach ($item in $PSBoundParameters.GetEnumerator()) { $bound[$item.Key] = $item.Value }
    $global:SdkCalls.Add([pscustomobject] @{ Command = 'Initialize-Segment'; Bound = $bound })
    return [pscustomobject] $bound
}

function global:Invoke-PatchInfraSegment {
    [CmdletBinding()]
    param([string] $SegmentId, [object] $Segment)
    $bound = [ordered] @{}
    foreach ($item in $PSBoundParameters.GetEnumerator()) { $bound[$item.Key] = $item.Value }
    $global:SdkCalls.Add([pscustomobject] @{ Command = 'Invoke-PatchInfraSegment'; Bound = $bound })
}

function global:Invoke-ReadIntentStatus {
    [CmdletBinding()]
    param([string] $IntentPath, [bool] $IncludeEnforcedStatus, [string] $SitePath)
    $bound = [ordered] @{}
    foreach ($item in $PSBoundParameters.GetEnumerator()) { $bound[$item.Key] = $item.Value }
    $global:SdkCalls.Add([pscustomobject] @{ Command = 'Invoke-ReadIntentStatus'; Bound = $bound })
    return [pscustomobject] @{
        consolidated_status = [pscustomobject] @{ consolidated_status = 'SUCCESS' }
    }
}

Import-Module $ModulePath -Force -WarningAction SilentlyContinue
$withOptionals = Set-VcfNsxInfraSegment `
    -SegmentId 'segment-one' `
    -DisplayName 'one' `
    -GatewayAddress '10.0.0.1/24' `
    -ConnectivityPath '/infra/tier-1s/t1' `
    -Description 'description' `
    -TransportZonePath '/infra/transport-zones/tz' `
    -AdminState DOWN `
    -OverlayId 0 `
    -VlanIds @('100', '200') `
    -PollIntervalMilliseconds 0
$withOptionalCalls = @($global:SdkCalls)

$global:SdkCalls = [Collections.Generic.List[object]]::new()
$withoutOptionals = Set-VcfNsxInfraSegment `
    -SegmentId 'segment-two' `
    -DisplayName 'two' `
    -GatewayAddress '10.0.1.1/24' `
    -ConnectivityPath '/infra/tier-1s/t2' `
    -PollIntervalMilliseconds 0
$withoutOptionalCalls = @($global:SdkCalls)

[ordered] @{
    withOptionals = [ordered] @{ result = $withOptionals; calls = $withOptionalCalls }
    withoutOptionals = [ordered] @{ result = $withoutOptionals; calls = $withoutOptionalCalls }
} | ConvertTo-Json -Depth 30 -Compress
'''

    with tempfile.TemporaryDirectory(prefix="vcf91-0049-powercli-") as temp_dir:
        script_path = Path(temp_dir) / "powercli.ps1"
        script_path.write_text(runner, encoding="utf-8", newline="\n")
        result = run(
            [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(script_path),
                str(MODULE_PATH),
            ]
        )

    require(result.returncode == 0, f"PowerCLI transport checks failed: {result.stderr.strip()}")
    stdout_lines = [line for line in result.stdout.splitlines() if line.strip()]
    require(len(stdout_lines) == 1, f"PowerCLI checks emitted unexpected output: {stdout_lines}")
    report = json.loads(stdout_lines[0])

    expected_commands = [
        "Initialize-SegmentSubnet",
        "Initialize-Segment",
        "Invoke-PatchInfraSegment",
        "Invoke-ReadIntentStatus",
    ]
    for case_name in ("withOptionals", "withoutOptionals"):
        case = report[case_name]
        require(case["result"]["Status"] == "SUCCESS", f"{case_name} production transport did not succeed")
        require(case["result"]["PollCount"] == 1, f"{case_name} production transport polled incorrectly")
        require([call["Command"] for call in case["calls"]] == expected_commands, f"{case_name} SDK call order differs")

    with_calls = report["withOptionals"]["calls"]
    require(set(with_calls[0]["Bound"]) == {"GatewayAddress"}, "subnet splat emitted an absent optional")
    require(
        with_calls[1]["Bound"]
        == {
            "DisplayName": "one",
            "ConnectivityPath": "/infra/tier-1s/t1",
            "Subnets": [{"GatewayAddress": "10.0.0.1/24"}],
            "Description": "description",
            "TransportZonePath": "/infra/transport-zones/tz",
            "AdminState": "DOWN",
            "OverlayId": 0,
            "VlanIds": ["100", "200"],
        },
        "production Segment splat did not preserve supplied optionals",
    )
    require(set(with_calls[2]["Bound"]) == {"SegmentId", "Segment"}, "PATCH SDK splat differs")
    require(with_calls[2]["Bound"]["SegmentId"] == "segment-one", "PATCH SegmentId differs")
    require(with_calls[3]["Bound"] == {"IntentPath": "/infra/segments/segment-one"}, "status SDK splat differs")

    without_calls = report["withoutOptionals"]["calls"]
    require(set(without_calls[0]["Bound"]) == {"GatewayAddress"}, "omitted subnet optional leaked into SDK splat")
    require(
        set(without_calls[1]["Bound"]) == {"DisplayName", "ConnectivityPath", "Subnets"},
        "omitted Segment optionals leaked into SDK splat",
    )
    require(without_calls[3]["Bound"] == {"IntentPath": "/infra/segments/segment-two"}, "unset status options leaked")


def exercise_loopback(contract: dict[str, Any]) -> None:
    runner = r'''
param(
    [string] $ModulePath,
    [string] $BaseUri,
    [string] $SegmentId,
    [string] $DisplayName,
    [string] $GatewayAddress,
    [string] $ConnectivityPath
)
$ErrorActionPreference = 'Stop'
Import-Module $ModulePath -Force
$transport = {
    param($Request)
    $uri = $BaseUri + [string] $Request.Path
    $queryParts = @()
    if ($null -ne $Request.Query) {
        foreach ($entry in $Request.Query.GetEnumerator()) {
            $queryParts += (
                [Uri]::EscapeDataString([string] $entry.Key) + '=' +
                [Uri]::EscapeDataString([string] $entry.Value)
            )
        }
    }
    if ($queryParts.Count -gt 0) {
        $uri += '?' + ($queryParts -join '&')
    }
    $invoke = @{
        Uri = $uri
        Method = [string] $Request.Method
        Headers = @{ Accept = 'application/json' }
    }
    if ($null -ne $Request.Body) {
        $invoke.ContentType = 'application/json'
        $invoke.Body = ConvertTo-Json -InputObject $Request.Body -Depth 20 -Compress
    }
    $httpResponse = Invoke-WebRequest @invoke
    if ([string]::IsNullOrWhiteSpace($httpResponse.Content)) {
        return $null
    }
    return ConvertFrom-Json -InputObject $httpResponse.Content -Depth 20
}.GetNewClosure()

$result = Set-VcfNsxInfraSegment `
    -SegmentId $SegmentId `
    -DisplayName $DisplayName `
    -GatewayAddress $GatewayAddress `
    -ConnectivityPath $ConnectivityPath `
    -PollIntervalMilliseconds 1 `
    -TimeoutSeconds 5 `
    -Transport $transport
$result | ConvertTo-Json -Depth 30 -Compress
'''

    values = scenario_values()
    with tempfile.TemporaryDirectory(prefix="vcf91-0049-run-") as temp_dir:
        temp = Path(temp_dir)
        log_path = temp / "requests.ndjson"
        ready_path = temp / "ready.txt"
        runner_path = temp / "scenario.ps1"
        runner_path.write_text(runner, encoding="utf-8", newline="\n")

        clean_env = os.environ.copy()
        for key in list(clean_env):
            if key.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
                clean_env.pop(key, None)
        clean_env["NO_PROXY"] = "127.0.0.1,localhost"
        mock = subprocess.Popen(
            [
                sys.executable,
                str(MOCK_PATH),
                "--contract",
                str(CONTRACT_PATH),
                "--log",
                str(log_path),
                "--ready-file",
                str(ready_path),
                "--port",
                "0",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=clean_env,
        )
        try:
            base_uri = wait_for_ready(ready_path, mock)
            require(re.fullmatch(r"http://127\.0\.0\.1:\d+/policy/api/v1", base_uri) is not None, "mock is not loopback-only")
            result = run(
                [
                    "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(runner_path),
                    str(MODULE_PATH),
                    base_uri,
                    values["segment_id"],
                    values["display_name"],
                    values["gateway_address"],
                    values["connectivity_path"],
                ],
                timeout=15,
                env=clean_env,
            )
        finally:
            mock.terminate()
            try:
                mock.wait(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.wait(timeout=2)

        require(result.returncode == 0, f"scenario failed:\nstdout: {result.stdout}\nstderr: {result.stderr}")
        stdout_lines = [line for line in result.stdout.splitlines() if line.strip()]
        require(len(stdout_lines) == 1, f"module emitted unexpected output: {stdout_lines}")
        summary = json.loads(stdout_lines[0])
        entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]

    patch_contract, status_contract = contract["operations"]
    require(len(entries) == 4, f"expected one PATCH and three status polls, got {len(entries)} requests")
    require(
        [entry["operationId"] for entry in entries]
        == [patch_contract["operationId"]] + [status_contract["operationId"]] * 3,
        "operation sequence is not PATCH followed by polling",
    )
    require([entry["sequence"] for entry in entries] == [1, 2, 3, 4], "request log sequence is unstable")

    patch_entry = entries[0]
    patch_path = contract["basePath"] + patch_contract["path"].replace("{segment-id}", quote(values["segment_id"], safe=""))
    require(patch_entry["method"] == patch_contract["method"], "wrong segment method")
    require(patch_entry["path"] == patch_path, "wrong segment path or escaping")
    require(patch_entry["raw_target"] == patch_path, "segment request contains an unexpected query")
    require(patch_entry["query"] == {}, "segment query must be empty")
    require(patch_entry["headers"].get("accept") == "application/json", "PATCH must request JSON")
    content_type_parts = [
        part.strip().lower()
        for part in patch_entry["headers"].get("content-type", "").split(";")
        if part.strip()
    ]
    require(content_type_parts and content_type_parts[0] == "application/json", "PATCH media type must be application/json")
    require(
        len(content_type_parts) <= 2
        and all(part == "charset=utf-8" for part in content_type_parts[1:]),
        "PATCH content type has an unexpected parameter",
    )

    expected_body = {
        "display_name": values["display_name"],
        "connectivity_path": values["connectivity_path"],
        "subnets": [{"gateway_address": values["gateway_address"]}],
    }
    expected_bytes = json.dumps(expected_body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    actual_bytes = patch_entry["body_utf8"].encode("utf-8")
    require(actual_bytes == expected_bytes, f"PATCH JSON wire bytes differ: {patch_entry['body_utf8']}")
    require(patch_entry["headers"].get("content-length") == str(len(expected_bytes)), "PATCH Content-Length does not match UTF-8 body")

    body = json.loads(patch_entry["body_utf8"])
    optional_segment_fields = {
        name
        for name, schema in patch_contract["request"]["properties"].items()
        if schema.get("optional")
    }
    require(
        optional_segment_fields.isdisjoint(body.keys() - expected_body.keys()),
        "an unset optional Segment field was emitted",
    )
    require("dhcp_ranges" not in body["subnets"][0], "unset dhcp_ranges must be omitted")

    intent_path = f"/infra/segments/{values['segment_id']}"
    status_path = contract["basePath"] + status_contract["path"]
    encoded_query = "intent_path=" + quote(intent_path, safe="")
    for entry in entries[1:]:
        require(entry["method"] == status_contract["method"], "wrong status method")
        require(entry["path"] == status_path, "wrong status path")
        require(entry["raw_target"] == status_path + "?" + encoded_query, "status query wire shape or escaping differs")
        require(entry["query"] == {"intent_path": [intent_path]}, "status query has missing or extra fields")
        require(entry["body_utf8"] == "", "GET poll must not send a body")
        require("content-type" not in entry["headers"], "GET poll must not send a content type for an absent body")
        require(entry["headers"].get("accept") == "application/json", "GET poll must request JSON")

    require(
        [entry["response_state"] for entry in entries[1:]]
        == ["IN_PROGRESS", "IN_PROGRESS", "SUCCESS"],
        "mock did not exercise a nonterminal-to-terminal transition",
    )
    require(summary["SegmentId"] == values["segment_id"], "summary SegmentId differs")
    require(summary["IntentPath"] == intent_path, "summary IntentPath differs")
    require(summary["Status"] == "SUCCESS", "workflow returned before SUCCESS")
    require(summary["PollCount"] == 3, "summary PollCount differs from requests")
    require(
        summary["Response"]["consolidated_status"]["consolidated_status"] == "SUCCESS",
        "final response was not preserved",
    )


def main() -> int:
    try:
        contract, _sources = load_contract()
        inspect_powershell()
        exercise_scripted_transport()
        exercise_powercli_transport()
        exercise_loopback(contract)
    except (AssertionError, KeyError, TypeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: VCF 9.1 NSX Policy async segment workflow matches the pinned wire contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
