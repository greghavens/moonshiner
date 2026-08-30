#!/usr/bin/env python3
"""Protected deterministic verification for the vSAN Data Protection module."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "src" / "VsanDataProtection"
MODULE_FILE = MODULE_DIR / "VsanDataProtection.psm1"
MANIFEST_FILE = MODULE_DIR / "VsanDataProtection.psd1"
CONTRACT_FILE = ROOT / "docs" / "contract.json"
SOURCES_FILE = ROOT / "docs" / "official_sources.json"
MOCK_FILE = ROOT / "mock" / "vsan_dp_mock.py"
COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
SPEC_PATH = "specifications/vsan-data-protection/vsan-data-protection-openapi.yaml"
OPERATION_IDS = [
    "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task",
    "Snapservice.Tasks_get",
]
SCENARIO_POLLS = {
    "lifecycle": 4,
    "immediate": 1,
    "failed": 1,
    "invalid": 1,
    "long": 130,
    "empty": 0,
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_protected_contract() -> dict:
    contract = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    require(contract["openapi"] == "3.0.3", "contract OpenAPI version drifted")
    require(contract["title"] == "Snapshot Appliance API", "contract title drifted")
    require(contract["version"] == "9.0.0.0", "contract is not the VCF 9.0 revision")
    require(list(contract["operations"]) == OPERATION_IDS, "contract operationIds drifted")
    require(
        contract["operations"][OPERATION_IDS[0]]["path"]
        == "/snapservice/clusters/{cluster}/protection-groups/{pg}/snapshots?vmw-task=true",
        "snapshot create path drifted",
    )
    require(
        contract["operations"][OPERATION_IDS[1]]["path"] == "/snapservice/tasks/{task}",
        "task lookup path drifted",
    )
    create_schema = contract["schemas"][
        "Snapservice.Clusters.ProtectionGroups.Snapshots.CreateSpec"
    ]
    require(create_schema["required"] == ["name"], "create required fields drifted")
    require(set(create_schema["properties"]) == {"name", "retention"}, "create fields drifted")
    require(
        contract["schemas"]["Snapservice.Tasks.Info"]["statusValues"]
        == ["PENDING", "RUNNING", "BLOCKED", "SUCCEEDED", "FAILED"],
        "task status values drifted",
    )
    require(sources["tag"] == "9.0.0.0", "source tag drifted")
    require(
        sources["repository"] == "https://github.com/vmware/vcf-api-specs",
        "source repository drifted",
    )
    require(sources["license"] == "Apache-2.0", "source license drifted")
    require(sources["commit"] == COMMIT, "source commit drifted")
    require(sources["specificationPath"] == SPEC_PATH, "source spec path drifted")
    require(sources["operationIds"] == OPERATION_IDS, "source operationIds drifted")
    require(
        sources["specificationUrl"]
        == f"https://raw.githubusercontent.com/vmware/vcf-api-specs/{COMMIT}/{SPEC_PATH}",
        "source URL is not the immutable 9.0 specification",
    )
    require("9.1" not in json.dumps(sources), "9.1 source must not be used")
    return contract


def verify_sdk_dependency() -> None:
    manifest = MANIFEST_FILE.read_text(encoding="utf-8")
    implementation = MODULE_FILE.read_text(encoding="utf-8")
    require("VMware.Sdk.Vcf.SddcManager" in manifest, "manifest must require the VCF SDK module")
    require("13.4.0.24798382" in manifest, "manifest must pin the VCF 9.0 SDK version")
    require(
        "Initialize-VcfAssociatedTask" in implementation,
        "implementation must use the generated SDK task model initializer",
    )
    require(
        "function initialize-vcfassociatedtask" not in implementation.casefold(),
        "implementation must not redefine the VMware SDK initializer",
    )
    forbidden_suffixes = {".dll", ".nupkg", ".nuspec"}
    authored_roots = (ROOT / "docs", ROOT / "mock", ROOT / "src", ROOT / "tests")
    vendored = [
        path
        for authored_root in authored_roots
        for path in authored_root.rglob("*")
        if path.suffix.lower() in forbidden_suffixes
    ]
    require(not vendored, "VMware SDK binaries or packages must not be vendored")


def wait_for_port(port_file: Path, process: subprocess.Popen[bytes]) -> int:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("loopback mock exited before publishing its port")
        if port_file.exists() and port_file.read_text(encoding="ascii").strip():
            return int(port_file.read_text(encoding="ascii"))
        time.sleep(0.02)
    raise AssertionError("loopback mock did not publish its port")


def invoke_module(servers: dict[str, str]) -> dict:
    script = r'''
$ErrorActionPreference = 'Stop'
$moduleFile = Join-Path $env:VSAN_DP_ROOT 'src/VsanDataProtection/VsanDataProtection.psm1'
$manifest = Join-Path $env:VSAN_DP_ROOT 'src/VsanDataProtection/VsanDataProtection.psd1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $moduleFile,
    [ref] $tokens,
    [ref] $parseErrors
)
$definitions = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -ceq 'New-VsanProtectionGroupSnapshot'
}, $true))
if ($parseErrors.Count -ne 0 -or $definitions.Count -ne 1) {
    throw 'The implementation must contain exactly one parseable target function.'
}
$signature = @($definitions[0].Body.ParamBlock.Parameters | ForEach-Object {
    [ordered]@{
        name = $_.Name.VariablePath.UserPath
        type = $_.StaticType.FullName
        defaultValue = if ($null -eq $_.DefaultValue) { $null } else { $_.DefaultValue.Extent.Text }
        attributes = @($_.Attributes | ForEach-Object { $_.Extent.Text })
    }
})
Import-Module $manifest -Force -ErrorAction Stop
$sdkCommand = Get-Command Initialize-VcfAssociatedTask -ErrorAction Stop
if ($sdkCommand.ModuleName -ne 'VMware.Sdk.Vcf.SddcManager') {
    throw 'The task initializer did not come from the genuine VMware SDK module.'
}
if ($sdkCommand.Version.ToString() -ne '13.4.0.24798382') {
    throw 'The task initializer came from the wrong VMware SDK version.'
}
$servers = ConvertFrom-Json $env:VSAN_DP_SERVERS -AsHashtable

function Invoke-Scenario {
    param([Parameter(Mandatory)][string] $Scenario)

    try {
        $result = New-VsanProtectionGroupSnapshot `
            -ServerUri ([uri] $servers[$Scenario]) `
            -SessionId 'session-secret' `
            -ClusterId 'domain-c8/alpha' `
            -ProtectionGroupId 'pg #42/blue' `
            -Name 'on-demand-2026-08-13' `
            -PollIntervalMilliseconds 0
        return [ordered]@{
            threw = $false
            message = $null
            result = $result
        }
    }
    catch {
        return [ordered]@{
            threw = $true
            message = $_.Exception.Message
            result = $null
        }
    }
}

$global:vsanDpSdkInitializerCalls = 0
$sdkBreakpoint = Set-PSBreakpoint -Command Initialize-VcfAssociatedTask -Action {
    $global:vsanDpSdkInitializerCalls++
}
$outcomes = [ordered]@{}
try {
    foreach ($scenario in @('lifecycle', 'immediate', 'failed', 'invalid', 'long', 'empty')) {
        $outcomes[$scenario] = Invoke-Scenario -Scenario $scenario
    }
}
finally {
    Remove-PSBreakpoint $sdkBreakpoint
}
$verification = [ordered]@{
    signature = $signature
    sdkInitializerCalls = $global:vsanDpSdkInitializerCalls
    outcomes = $outcomes
}
'RESULT_JSON=' + ($verification | ConvertTo-Json -Depth 10 -Compress)
'''
    environment = os.environ.copy()
    environment["VSAN_DP_ROOT"] = str(ROOT)
    environment["VSAN_DP_SERVERS"] = json.dumps(servers, separators=(",", ":"))
    completed = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=45,
        check=False,
    )
    require(completed.returncode == 0, "PowerShell invocation failed:\n" + completed.stdout)
    marker_lines = [line for line in completed.stdout.splitlines() if line.startswith("RESULT_JSON=")]
    require(len(marker_lines) == 1, "PowerShell did not return exactly one result marker")
    return json.loads(marker_lines[0].split("=", 1)[1])


def read_log(log_file: Path) -> list[dict]:
    return [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line]


def verify_signature(signature: list[dict]) -> None:
    expected = [
        ("ServerUri", "System.Uri", None, ["[Parameter(Mandatory)]", "[uri]"]),
        (
            "SessionId",
            "System.String",
            None,
            ["[Parameter(Mandatory)]", "[ValidateNotNullOrEmpty()]", "[string]"],
        ),
        (
            "ClusterId",
            "System.String",
            None,
            ["[Parameter(Mandatory)]", "[ValidateNotNullOrEmpty()]", "[string]"],
        ),
        (
            "ProtectionGroupId",
            "System.String",
            None,
            ["[Parameter(Mandatory)]", "[ValidateNotNullOrEmpty()]", "[string]"],
        ),
        (
            "Name",
            "System.String",
            None,
            ["[Parameter(Mandatory)]", "[ValidateNotNullOrEmpty()]", "[string]"],
        ),
        (
            "PollIntervalMilliseconds",
            "System.Int32",
            "0",
            ["[Parameter()]", "[ValidateRange(0, [int]::MaxValue)]", "[int]"],
        ),
    ]

    def normalized_attributes(values: list[str]) -> list[str]:
        return ["".join(value.split()).casefold() for value in values]

    actual = [
        (
            parameter["name"],
            parameter["type"],
            parameter["defaultValue"],
            normalized_attributes(parameter["attributes"]),
        )
        for parameter in signature
    ]
    normalized_expected = [
        (name, type_name, default, normalized_attributes(attributes))
        for name, type_name, default, attributes in expected
    ]
    require(actual == normalized_expected, "the supplied function signature must not change")


def verify_wire(requests: list[dict], contract: dict, expected_polls: int) -> None:
    require(
        len(requests) == expected_polls + 1,
        f"expected create plus {expected_polls} task polls, got {len(requests)} requests",
    )
    create, *polls = requests
    expected_create = (
        contract["basePath"]
        + contract["operations"][OPERATION_IDS[0]]["path"]
        .replace("{cluster}", quote("domain-c8/alpha", safe=""))
        .replace("{pg}", quote("pg #42/blue", safe=""))
    )
    expected_task = (
        contract["basePath"]
        + contract["operations"][OPERATION_IDS[1]]["path"].replace(
            "{task}", quote("task 17/blue", safe="")
        )
    )
    require(create["method"] == "POST", "create method must be POST")
    require(create["target"] == expected_create, "create request target has the wrong wire shape")
    require(create["body"] == '{"name":"on-demand-2026-08-13"}', "create JSON is not exact and compact")
    require(json.loads(create["body"]) == {"name": "on-demand-2026-08-13"}, "unexpected create body")
    require("retention" not in create["body"], "unset optional retention must be omitted")
    require(create["headers"].get("accept") == "application/json", "create Accept header is wrong")
    require(
        create["headers"].get("content-type") == "application/json",
        "create Content-Type header is wrong",
    )
    require(
        create["headers"].get("vmware-api-session-id") == "session-secret",
        "create session header is wrong",
    )
    for index, request in enumerate(polls, start=1):
        require(request["method"] == "GET", f"task poll {index} must be GET")
        require(request["target"] == expected_task, f"task poll {index} used the wrong task")
        require(request["body"] == "", f"task poll {index} must not have a body")
        require("content-type" not in request["headers"], f"task poll {index} must omit Content-Type")
        require(request["headers"].get("accept") == "application/json", f"poll {index} Accept is wrong")
        require(
            request["headers"].get("vmware-api-session-id") == "session-secret",
            f"poll {index} session header is wrong",
        )


def main() -> None:
    contract = verify_protected_contract()
    verify_sdk_dependency()
    with tempfile.TemporaryDirectory(prefix="vsan-dp-verify-") as temp_name:
        temp_dir = Path(temp_name)
        processes: dict[str, subprocess.Popen[bytes]] = {}
        log_files: dict[str, Path] = {}
        servers: dict[str, str] = {}
        try:
            for scenario in SCENARIO_POLLS:
                scenario_dir = temp_dir / scenario
                scenario_dir.mkdir()
                log_file = scenario_dir / "requests.jsonl"
                port_file = scenario_dir / "port"
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-B",
                        str(MOCK_FILE),
                        "--contract",
                        str(CONTRACT_FILE),
                        "--log",
                        str(log_file),
                        "--port-file",
                        str(port_file),
                        "--scenario",
                        scenario,
                    ],
                    cwd=ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                processes[scenario] = process
                log_files[scenario] = log_file
                port = wait_for_port(port_file, process)
                servers[scenario] = f"http://127.0.0.1:{port}"
            verification = invoke_module(servers)
        finally:
            for process in processes.values():
                process.terminate()
            for process in processes.values():
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        requests_by_scenario = {
            scenario: read_log(log_file) for scenario, log_file in log_files.items()
        }

    for scenario, expected_polls in SCENARIO_POLLS.items():
        verify_wire(requests_by_scenario[scenario], contract, expected_polls)

    verify_signature(verification["signature"])
    require(
        verification["sdkInitializerCalls"] >= 5,
        "each non-empty create response must be passed to the genuine SDK task initializer",
    )
    outcomes = verification["outcomes"]
    for scenario in ("lifecycle", "immediate", "long"):
        outcome = outcomes[scenario]
        require(not outcome["threw"], f"{scenario} scenario unexpectedly threw: {outcome['message']}")
        require(
            outcome["result"]["status"] == "SUCCEEDED",
            f"{scenario} scenario did not return the terminal task object",
        )
        require(
            outcome["result"]["result"]["snapshot"] == "snapshot-9001",
            f"{scenario} scenario lost the terminal task result",
        )

    for scenario in ("failed", "invalid", "empty"):
        outcome = outcomes[scenario]
        require(outcome["threw"], f"{scenario} scenario must throw")
        require(outcome["result"] is None, f"{scenario} scenario must not return a result")
    print(
        "PASS: contract, genuine SDK integration, exact wire shape, task lifecycle, "
        "terminal outcomes, and unbounded polling verified"
    )


if __name__ == "__main__":
    main()
