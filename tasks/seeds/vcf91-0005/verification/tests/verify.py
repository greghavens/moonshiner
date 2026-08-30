#!/usr/bin/env python3
"""Protected deterministic verifier for vcf91-0005."""

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
MODULE_MANIFEST = ROOT / "src" / "VcfLifecycleConnectivity.psd1"
MODULE_SOURCE = ROOT / "src" / "VcfLifecycleConnectivity.psm1"
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MOCK_PATH = ROOT / ".moonshiner" / "mock_sddc_manager.py"

EXPECTED_COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
EXPECTED_SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
EXPECTED_OPERATIONS = [
    ("createToken", "POST", "/v1/tokens"),
    ("updateProxyConfiguration", "PATCH", "/v1/system/proxy-configuration"),
    ("getTask", "GET", "/v1/tasks/{id}"),
    ("updateDepotSettings", "PUT", "/v1/system/settings/depot"),
]


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def verify_contract_provenance() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))

    require(contract.get("openapi") == "3.0.1", "contract must identify OpenAPI 3.0.1")
    require(contract.get("info", {}).get("version") == "9.1.0.0", "contract must pin VCF 9.1.0.0")
    require(contract.get("derivedFrom", {}).get("commit") == EXPECTED_COMMIT, "contract commit changed")
    require(contract.get("derivedFrom", {}).get("path") == EXPECTED_SPEC_PATH, "contract spec path changed")
    require(sources.get("commit") == EXPECTED_COMMIT, "official source commit changed")
    require(sources.get("specPath") == EXPECTED_SPEC_PATH, "official source spec path changed")
    require(sources.get("license") == "Apache-2.0", "official source license must be Apache-2.0")

    contract_ops = [
        (entry["operationId"], entry["method"], entry["path"])
        for entry in contract.get("operations", [])
    ]
    source_ops = [
        (entry["operationId"], entry["method"], entry["path"])
        for entry in sources.get("operations", [])
    ]
    require(contract_ops == EXPECTED_OPERATIONS, "contract operation set/order changed")
    require(source_ops == EXPECTED_OPERATIONS, "official_sources operation set/order changed")

    proxy = contract["schemas"]["ProxyConfiguration"]
    require(proxy.get("required") == [], "ProxyConfiguration has no OpenAPI required fields")
    require(proxy["properties"]["isConfigured"].get("readOnly") is True, "isConfigured must remain read-only")
    require(proxy["properties"]["transferProtocol"].get("enum") == ["HTTP", "HTTPS"], "proxy protocol enum drifted")
    account = contract["schemas"]["DepotAccount"]
    require(account["properties"]["downloadToken"].get("maxLength") == 32, "downloadToken constraint drifted")


def verify_module_source() -> None:
    require(MODULE_MANIFEST.is_file(), "module manifest is missing")
    require(MODULE_SOURCE.is_file(), "implement src/VcfLifecycleConnectivity.psm1")
    required_commands = [
        "Initialize-VcfProxyConfiguration",
        "Invoke-VcfUpdateProxyConfiguration",
        "Invoke-VcfGetTask",
        "Initialize-VcfDepotAccount",
        "Initialize-VcfDepotSettings",
        "Invoke-VcfUpdateDepotSettings",
    ]
    pwsh = shutil.which("pwsh")
    require(pwsh is not None, "pwsh prerequisite is missing")
    ast_script = r"""
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:VCF_MODULE_SOURCE,
    [ref]$tokens,
    [ref]$parseErrors
)
$commands = @(
    $ast.FindAll(
        { param($node) $node -is [System.Management.Automation.Language.CommandAst] },
        $true
    ) | ForEach-Object { $_.GetCommandName() } | Where-Object { $null -ne $_ }
)
$functions = @(
    $ast.FindAll(
        { param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] },
        $true
    ) | ForEach-Object { $_.Name }
)
$manifest = Import-PowerShellDataFile -LiteralPath $env:VCF_MODULE_MANIFEST
$requiredModules = @(
    $manifest.RequiredModules | ForEach-Object {
        if ($_ -is [string]) { $_ } else { $_.ModuleName }
    }
)
[pscustomobject]@{
    ParseErrorCount = @($parseErrors).Count
    Commands = $commands
    Functions = $functions
    RootModule = $manifest.RootModule
    RequiredModules = $requiredModules
    FunctionsToExport = @($manifest.FunctionsToExport)
} | ConvertTo-Json -Depth 6 -Compress
"""
    environment = os.environ.copy()
    environment["VCF_MODULE_SOURCE"] = str(MODULE_SOURCE)
    environment["VCF_MODULE_MANIFEST"] = str(MODULE_MANIFEST)
    parsed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", ast_script],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    require(parsed.returncode == 0, f"PowerShell source inspection failed: {parsed.stderr}")
    source_contract = json.loads(parsed.stdout)
    require(source_contract.get("ParseErrorCount") == 0, "module contains PowerShell parse errors")
    commands = source_contract.get("Commands") or []
    functions = source_contract.get("Functions") or []
    for command in required_commands:
        require(command in commands, f"module must call {command}")
        require(command not in functions, f"module must not redefine VMware SDK command {command}")
    public_function = "Invoke-VcfLifecycleConnectivityChange"
    require(functions.count(public_function) == 1, "required exported function must be defined exactly once")
    require("Export-ModuleMember" in commands, "module must explicitly export its public function")

    forbidden_commands = {
        "Invoke-RestMethod",
        "Invoke-WebRequest",
        "Start-Process",
        "curl",
        "curl.exe",
        "wget",
        "Invoke-Expression",
    }
    used_forbidden = sorted(forbidden_commands.intersection(commands))
    require(not used_forbidden, f"module invokes forbidden transport/process commands: {used_forbidden}")

    text = MODULE_SOURCE.read_text(encoding="utf-8")
    lowered = text.casefold()
    forbidden_types = [
        "system.net.http",
        "httpclient",
        "httpwebrequest",
        "webclient",
        "tcpclient",
        "system.net.sockets",
    ]
    for token in forbidden_types:
        require(token not in lowered, f"module must not use raw transport type {token}")
    require("/v1/" not in lowered, "module must not hard-code REST paths")
    require(source_contract.get("RootModule") == "VcfLifecycleConnectivity.psm1", "manifest RootModule changed")
    require(source_contract.get("RequiredModules") == ["VMware.Sdk.Vcf.SddcManager"],
            "manifest must require only VMware.Sdk.Vcf.SddcManager")
    require(source_contract.get("FunctionsToExport") == [public_function],
            "manifest must export only Invoke-VcfLifecycleConnectivityChange")


def wait_for_ready(path: Path, process: subprocess.Popen[str]) -> dict[str, object]:
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size:
            return json.loads(path.read_text(encoding="utf-8"))
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise VerificationError(f"loopback fixture exited early\nstdout: {stdout}\nstderr: {stderr}")
        time.sleep(0.03)
    raise VerificationError("loopback fixture did not become ready")


def invoke_candidate(ready: dict[str, object]) -> dict[str, object]:
    pwsh = shutil.which("pwsh")
    require(pwsh is not None, "pwsh prerequisite is missing")

    discovery = subprocess.run(
        [
            pwsh,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "if (Get-Module -ListAvailable VMware.Sdk.Vcf.SddcManager) { 'yes' } else { 'no' }",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    require(discovery.returncode == 0 and discovery.stdout.strip().endswith("yes"),
            "VMware.Sdk.Vcf.SddcManager prerequisite is not installed")

    script = r"""
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $env:VCF_TEST_ROOT 'src/VcfLifecycleConnectivity.psd1') -Force
$exports = @(
    Get-Command -Module VcfLifecycleConnectivity -CommandType Function |
        Select-Object -ExpandProperty Name
)
if (($exports -join ',') -cne 'Invoke-VcfLifecycleConnectivityChange') {
    throw "Module exports changed: $($exports -join ',')"
}
foreach ($sdkCommand in @(
    'Initialize-VcfProxyConfiguration',
    'Invoke-VcfUpdateProxyConfiguration',
    'Invoke-VcfGetTask',
    'Initialize-VcfDepotAccount',
    'Initialize-VcfDepotSettings',
    'Invoke-VcfUpdateDepotSettings'
)) {
    if ((Get-Command $sdkCommand -ErrorAction Stop).Source -cne 'VMware.Sdk.Vcf.SddcManager') {
        throw "$sdkCommand was not resolved from the genuine VMware SDK module."
    }
}
$password = ConvertTo-SecureString $env:VCF_MOCK_PASSWORD -AsPlainText -Force
$connection = Connect-VcfSddcManagerServer `
    -Server '127.0.0.1' `
    -Port ([int]$env:VCF_MOCK_PORT) `
    -Protocol 'http' `
    -User $env:VCF_MOCK_USERNAME `
    -Password $password `
    -NotDefault
$result = Invoke-VcfLifecycleConnectivityChange `
    -Server $connection `
    -ProxyHost $env:VCF_PROXY_HOST `
    -ProxyPort ([int]$env:VCF_PROXY_PORT) `
    -ProxyProtocol $env:VCF_PROXY_PROTOCOL `
    -DepotDownloadToken $env:VCF_DEPOT_TOKEN
'MOONSHINER_RESULT:' + ($result | ConvertTo-Json -Depth 20 -Compress)
"""
    environment = os.environ.copy()
    environment["VCF_TEST_ROOT"] = str(ROOT)
    environment["VCF_MOCK_PORT"] = str(ready["port"])
    environment["VCF_MOCK_USERNAME"] = str(ready["username"])
    environment["VCF_MOCK_PASSWORD"] = str(ready["password"])
    environment["VCF_PROXY_HOST"] = str(ready["proxyHost"])
    environment["VCF_PROXY_PORT"] = str(ready["proxyPort"])
    environment["VCF_PROXY_PROTOCOL"] = str(ready["proxyProtocol"])
    environment["VCF_DEPOT_TOKEN"] = str(ready["depotToken"])
    run = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )
    require(
        run.returncode == 0,
        "PowerShell scenario failed instead of returning a report\n"
        f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}",
    )
    marker = "MOONSHINER_RESULT:"
    lines = [line for line in run.stdout.splitlines() if line.startswith(marker)]
    require(len(lines) == 1, f"expected exactly one result object, stdout was:\n{run.stdout}")
    return json.loads(lines[0][len(marker):])


def verify_result(result: dict[str, object], ready: dict[str, object]) -> None:
    require(result.get("Outcome") == "PartialFailure", "overall Outcome must be PartialFailure")
    steps = result.get("Steps")
    require(isinstance(steps, list) and len(steps) == 2, "Steps must be an ordered two-element array")

    proxy, depot = steps
    require(proxy.get("Name") == "Proxy", "first report must be Proxy")
    require(proxy.get("OperationId") == "updateProxyConfiguration", "proxy operationId is wrong")
    require(proxy.get("Status") == "Succeeded", "proxy success was not preserved")
    require(proxy.get("TaskId") == ready["taskId"], "proxy task ID was not preserved")
    require(proxy.get("TaskStatus") == "SUCCESSFUL", "proxy terminal status was not preserved")
    require(proxy.get("ErrorCode") is None and proxy.get("ErrorMessage") is None,
            "successful proxy report must not contain an error")

    require(depot.get("Name") == "Depot", "second report must be Depot")
    require(depot.get("OperationId") == "updateDepotSettings", "depot operationId is wrong")
    require(depot.get("Status") == "Failed", "depot failure was not reported")
    require(depot.get("TaskId") is None and depot.get("TaskStatus") is None,
            "failed synchronous depot call must not invent task data")
    require(depot.get("ErrorCode") == "DEPOT_TOKEN_REJECTED", "structured depot error code was not retained")
    require(depot.get("ErrorMessage") == ready["failureMessage"], "depot error message was not retained")


def read_request_log(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def verify_wire(requests: list[dict[str, object]], ready: dict[str, object]) -> None:
    expected = [
        ("createToken", "POST", "/v1/tokens"),
        (None, "GET", "/v1/sddc-manager"),
        ("updateProxyConfiguration", "PATCH", "/v1/system/proxy-configuration"),
        ("getTask", "GET", f"/v1/tasks/{ready['taskId']}"),
        ("updateDepotSettings", "PUT", "/v1/system/settings/depot"),
    ]
    observed = [(item.get("operationId"), item.get("method"), item.get("path")) for item in requests]
    require(observed == expected, f"wrong request sequence or unexpected request: {observed!r}")
    require(all(item.get("query") == "" for item in requests), "no operation in this contract uses a query string")

    token, probe, proxy, task, depot = requests
    require(token.get("jsonBody") == {"username": ready["username"], "password": ready["password"]},
            "createToken wire body must contain only username and password; apiKey/idToken must be omitted")
    require(proxy.get("jsonBody") == {
        "isEnabled": True,
        "host": ready["proxyHost"],
        "port": ready["proxyPort"],
        "transferProtocol": ready["proxyProtocol"],
    }, "proxy wire body has a missing, extra, empty, or incorrectly typed field")
    require(depot.get("jsonBody") == {
        "vmwareAccount": {"downloadToken": ready["depotToken"]}
    }, "depot wire body must omit every unset DepotAccount/DepotSettings field")
    require(probe.get("rawBody") == "", "SDK version probe must not send a request body")
    require(task.get("rawBody") == "", "getTask must not send a request body")

    for item in (token, proxy, depot):
        content_type = str(item.get("headers", {}).get("content-type", "")).lower()
        require(content_type.startswith("application/json"), "JSON mutations must send application/json")
    for item in (probe, proxy, task, depot):
        authorization = str(item.get("headers", {}).get("authorization", ""))
        require(authorization == f"Bearer {ready['accessToken']}",
                "authenticated calls must use the SDK bearer token")


def main() -> int:
    try:
        verify_contract_provenance()
        verify_module_source()
        with tempfile.TemporaryDirectory(prefix="vcf91-0005-") as temp_name:
            temp = Path(temp_name)
            request_log = temp / "requests.jsonl"
            ready_file = temp / "ready.json"
            server = subprocess.Popen(
                [
                    sys.executable,
                    str(MOCK_PATH),
                    "--port",
                    "0",
                    "--request-log",
                    str(request_log),
                    "--ready-file",
                    str(ready_file),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                ready = wait_for_ready(ready_file, server)
                require(ready.get("host") == "127.0.0.1", "fixture must bind only to loopback")
                result = invoke_candidate(ready)
                verify_result(result, ready)
                verify_wire(read_request_log(request_log), ready)
            finally:
                server.terminate()
                try:
                    server.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.communicate(timeout=3)
        print("PASS: VCF partial-failure report and exact loopback wire contract verified")
        return 0
    except (VerificationError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
