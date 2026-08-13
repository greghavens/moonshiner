#!/usr/bin/env python3
"""Protected deterministic acceptance verifier for the VCF Installer module."""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
POLL_INTERVAL = 17


def fail(message: str) -> None:
    raise AssertionError(message)


def verify_contract() -> None:
    contract = json.loads((ROOT / "docs" / "contract.json").read_text(encoding="utf-8"))
    sources = json.loads((ROOT / "docs" / "official_sources.json").read_text(encoding="utf-8"))
    if contract.get("info", {}).get("version") != "9.0.0.0":
        fail("contract must remain pinned to VCF Installer 9.0.0.0")
    expected = {
        "startBundleDownloadByID": ("PATCH", "/v1/bundles/{id}"),
        "getTask": ("GET", "/v1/tasks/{id}"),
    }
    operations = {
        operation.get("operationId"): (method.upper(), path)
        for path, path_item in contract.get("paths", {}).items()
        for method, operation in path_item.items()
        if isinstance(operation, dict) and operation.get("operationId")
    }
    if operations != expected:
        fail("contract operation set changed")
    bundle_properties = contract["components"]["schemas"]["BundleDownloadSpec"]["properties"]
    if set(bundle_properties) != {"scheduledTimestamp", "downloadNow", "cancelNow"}:
        fail("BundleDownloadSpec optional fields changed")
    if sources.get("tag") != "9.0.0.0" or sources.get("commit") != EXPECTED_COMMIT:
        fail("official source tag or commit changed")
    if sources.get("specPath") != "specifications/vcf-installer/vcf-installer-openapi.json":
        fail("official source spec path changed")
    source_ops = {
        item.get("operationId"): (item.get("method"), item.get("path"))
        for item in sources.get("operations", [])
    }
    if source_ops != expected:
        fail("official source operation records changed")


def verify_source_shape() -> None:
    manifest = (ROOT / "src" / "VcfInstaller.Async.psd1").read_text(encoding="utf-8")
    module = (ROOT / "src" / "VcfInstaller.Async.psm1").read_text(encoding="utf-8")
    if "VMware.Sdk.Vcf.Installer" not in manifest:
        fail("module manifest must require VMware.Sdk.Vcf.Installer")
    for command in (
        "Initialize-VcfInstallerBundleDownloadSpec",
        "Initialize-VcfInstallerBundleUpdateSpec",
    ):
        if not re.search(rf"(?i)(?<![\w-]){re.escape(command)}(?![\w-])", module):
            fail(f"implementation must use {command}")
    vendored = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and "VMware.Sdk.Vcf" in path.name
        and path.parent != ROOT / "src"
    ]
    if vendored:
        fail(f"VMware SDK modules must not be vendored: {vendored}")


def powershell_script(
    bundle_id: str,
    token_value: str,
    poll_interval: int | None,
) -> str:
    module = (ROOT / "src" / "VcfInstaller.Async.psm1").as_posix().replace("'", "''")
    bundle = bundle_id.replace("'", "''")
    token = token_value.replace("'", "''")
    poll_assignment = (
        f"$invokeParameters.PollIntervalMilliseconds = {poll_interval}"
        if poll_interval is not None
        else ""
    )
    return f"""
$ErrorActionPreference = 'Stop'
$global:VcfInitializerCalls = [System.Collections.Generic.List[object]]::new()
$global:VcfSleepCalls = [System.Collections.Generic.List[object]]::new()
$global:VcfDownloadSpec = $null

# The production manifest pins the genuine SDK dependency. The verifier imports
# the implementation directly and supplies a module-scoped fixture for only the
# two generated model initializers so the seed remains self-contained and
# accepts either ordinary or module-qualified command invocation.
$initializerFixture = New-Module -Name VMware.Sdk.Vcf.Installer -ScriptBlock {{
    function Initialize-VcfInstallerBundleDownloadSpec {{
        [CmdletBinding()]
        param([Parameter(Mandatory)][bool] $DownloadNow)

        $global:VcfInitializerCalls.Add([pscustomobject][ordered]@{{
            name = 'Initialize-VcfInstallerBundleDownloadSpec'
            downloadNow = $DownloadNow
        }})
        $global:VcfDownloadSpec = [pscustomobject][ordered]@{{
            ScheduledTimestamp = $null
            DownloadNow = $DownloadNow
            CancelNow = $false
        }}
        return $global:VcfDownloadSpec
    }}

    function Initialize-VcfInstallerBundleUpdateSpec {{
        [CmdletBinding()]
        param([Parameter(Mandatory)][object] $BundleDownloadSpec)

        $global:VcfInitializerCalls.Add([pscustomobject][ordered]@{{
            name = 'Initialize-VcfInstallerBundleUpdateSpec'
            sameSpec = [object]::ReferenceEquals($BundleDownloadSpec, $global:VcfDownloadSpec)
        }})
        return [pscustomobject][ordered]@{{
            BundleDownloadSpec = $BundleDownloadSpec
        }}
    }}

    Export-ModuleMember -Function @(
        'Initialize-VcfInstallerBundleDownloadSpec',
        'Initialize-VcfInstallerBundleUpdateSpec'
    )
}}
Import-Module $initializerFixture -Force

function global:Start-Sleep {{
    [CmdletBinding(DefaultParameterSetName = 'Seconds')]
    param(
        [Parameter(Position = 0, ParameterSetName = 'Seconds')]
        [double] $Seconds,

        [Parameter(Mandatory, ParameterSetName = 'Milliseconds')]
        [int] $Milliseconds
    )
    if ($PSCmdlet.ParameterSetName -eq 'Milliseconds') {{
        $global:VcfSleepCalls.Add([pscustomobject]@{{
            parameterSet = 'Milliseconds'
            value = $Milliseconds
        }})
    }} else {{
        $global:VcfSleepCalls.Add([pscustomobject]@{{
            parameterSet = 'Seconds'
            value = $Seconds
        }})
    }}
}}

Import-Module '{module}' -Force

$command = Get-Command Start-VcfInstallerBundleDownload -ErrorAction Stop
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    '{module}', [ref] $tokens, [ref] $parseErrors
)
$functionAsts = @($ast.FindAll({{
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Start-VcfInstallerBundleDownload'
}}, $true))
if ($parseErrors.Count -ne 0 -or $functionAsts.Count -ne 1) {{
    throw 'The module must contain one parseable Start-VcfInstallerBundleDownload function.'
}}
$functionAst = $functionAsts[0]
$pollAst = @($functionAst.Body.ParamBlock.Parameters | Where-Object {{
    $_.Name.VariablePath.UserPath -eq 'PollIntervalMilliseconds'
}})[0]
$pollRange = @($command.Parameters['PollIntervalMilliseconds'].Attributes | Where-Object {{
    $_ -is [System.Management.Automation.ValidateRangeAttribute]
}})[0]
$metadata = [ordered]@{{
    cmdletBinding = [bool] $command.CmdletBinding
    declaredParameters = @($functionAst.Body.ParamBlock.Parameters | ForEach-Object {{
        $_.Name.VariablePath.UserPath
    }})
    serverUriType = $command.Parameters['ServerUri'].ParameterType.FullName
    accessTokenType = $command.Parameters['AccessToken'].ParameterType.FullName
    bundleIdType = $command.Parameters['BundleId'].ParameterType.FullName
    pollIntervalType = $command.Parameters['PollIntervalMilliseconds'].ParameterType.FullName
    serverUriMandatory = [bool] @($command.Parameters['ServerUri'].Attributes | Where-Object {{
        $_ -is [System.Management.Automation.ParameterAttribute]
    }})[0].Mandatory
    accessTokenMandatory = [bool] @($command.Parameters['AccessToken'].Attributes | Where-Object {{
        $_ -is [System.Management.Automation.ParameterAttribute]
    }})[0].Mandatory
    bundleIdMandatory = [bool] @($command.Parameters['BundleId'].Attributes | Where-Object {{
        $_ -is [System.Management.Automation.ParameterAttribute]
    }})[0].Mandatory
    pollIntervalMandatory = [bool] @($command.Parameters['PollIntervalMilliseconds'].Attributes | Where-Object {{
        $_ -is [System.Management.Automation.ParameterAttribute]
    }})[0].Mandatory
    pollIntervalMinimum = $pollRange.MinRange
    pollIntervalMaximum = $pollRange.MaxRange
    pollIntervalDefault = $pollAst.DefaultValue.SafeGetValue()
}}
Write-Output ('VCF_METADATA:' + ($metadata | ConvertTo-Json -Depth 6 -Compress))

$invokeParameters = @{{
    ServerUri = $env:VCF_INSTALLER_MOCK_URL
    AccessToken = '{token}'
    BundleId = '{bundle}'
}}
{poll_assignment}
$result = Start-VcfInstallerBundleDownload @invokeParameters
Write-Output ('VCF_RESULT:' + ($result | ConvertTo-Json -Depth 12 -Compress))
Write-Output ('VCF_INITIALIZERS:' + (ConvertTo-Json -InputObject $global:VcfInitializerCalls -Depth 6 -Compress))
Write-Output ('VCF_SLEEPS:' + (ConvertTo-Json -InputObject $global:VcfSleepCalls -Compress))
"""


def read_marker(stdout: str, prefix: str) -> object:
    values = [
        line.removeprefix(prefix)
        for line in stdout.splitlines()
        if line.startswith(prefix)
    ]
    if len(values) != 1:
        fail(f"expected one {prefix} marker, got stdout: {stdout}")
    try:
        return json.loads(values[0])
    except json.JSONDecodeError as error:
        fail(f"invalid JSON in {prefix} marker: {error}")


def verify_metadata(value: object) -> None:
    if not isinstance(value, dict):
        fail(f"command metadata must be an object, got {value!r}")
    declared = value.get("declaredParameters")
    expected_declared = {
        "ServerUri",
        "AccessToken",
        "BundleId",
        "PollIntervalMilliseconds",
    }
    if not isinstance(declared, list) or len(declared) != 4 or set(declared) != expected_declared:
        fail(f"declared parameter set is incorrect: {declared!r}")
    value_without_declared = dict(value)
    value_without_declared.pop("declaredParameters")
    expected = {
        "cmdletBinding": True,
        "serverUriType": "System.Uri",
        "accessTokenType": "System.String",
        "bundleIdType": "System.String",
        "pollIntervalType": "System.Int32",
        "serverUriMandatory": True,
        "accessTokenMandatory": True,
        "bundleIdMandatory": True,
        "pollIntervalMandatory": False,
        "pollIntervalMinimum": 0,
        "pollIntervalMaximum": 2_147_483_647,
        "pollIntervalDefault": 0,
    }
    if value_without_declared != expected:
        fail(f"advanced-function parameter contract is incorrect: {value!r}")


def verify_initializer_calls(value: object) -> None:
    expected = [
        {
            "name": "Initialize-VcfInstallerBundleDownloadSpec",
            "downloadNow": True,
        },
        {
            "name": "Initialize-VcfInstallerBundleUpdateSpec",
            "sameSpec": True,
        },
    ]
    if value != expected:
        fail(f"SDK request-model initializers were not used correctly: {value!r}")


def read_log(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def verify_wire(
    records: list[dict[str, object]],
    *,
    bundle_id: str,
    task_id: str,
    token: str,
    expected_poll_count: int,
) -> None:
    if len(records) != 1 + expected_poll_count:
        fail(
            f"expected one submit and {expected_poll_count} polls, "
            f"got {len(records)} requests: {records}"
        )
    submit, *polls = records
    if (submit.get("method"), submit.get("path"), submit.get("query")) != (
        "PATCH",
        f"/v1/bundles/{quote(bundle_id, safe='')}",
        "",
    ):
        fail(f"incorrect submit request target: {submit}")
    try:
        body = json.loads(str(submit.get("body", "")))
    except json.JSONDecodeError as error:
        fail(f"submit body is not JSON: {error}")
    expected_body = {"bundleDownloadSpec": {"downloadNow": True}}
    if body != expected_body:
        fail(f"submit JSON must be exactly {expected_body!r}, got {body!r}")
    download_spec = body["bundleDownloadSpec"]
    for omitted in ("scheduledTimestamp", "cancelNow"):
        if omitted in download_spec:
            fail(f"unset optional field {omitted} must be omitted")
    headers = submit.get("headers", {})
    assert isinstance(headers, dict)
    if headers.get("authorization") != f"Bearer {token}":
        fail("submit request must use bearer authentication")
    if not str(headers.get("content-type", "")).lower().startswith("application/json"):
        fail("submit request must use application/json content type")
    if "application/json" not in str(headers.get("accept", "")).lower():
        fail("submit request must accept application/json")
    for index, poll in enumerate(polls, start=1):
        if (poll.get("method"), poll.get("path"), poll.get("query"), poll.get("body")) != (
            "GET",
            f"/v1/tasks/{quote(task_id, safe='')}",
            "",
            "",
        ):
            fail(f"incorrect poll {index} wire shape: {poll}")
        poll_headers = poll.get("headers", {})
        assert isinstance(poll_headers, dict)
        if poll_headers.get("authorization") != f"Bearer {token}":
            fail(f"poll {index} must use bearer authentication")
        if "application/json" not in str(poll_headers.get("accept", "")).lower():
            fail(f"poll {index} must accept application/json")


def run_acceptance_case(
    *,
    bundle_id: str,
    task_id: str,
    token: str,
    poll_interval: int | None,
    expected_sleeps: list[dict[str, object]],
    submit_status: str,
    expected_poll_count: int,
) -> None:
    mock_script = ROOT / "tests" / "mock_vcf_installer.py"
    contract = ROOT / "docs" / "contract.json"
    with tempfile.TemporaryDirectory(prefix="vcf90-verifier-") as temp:
        log_path = Path(temp) / "requests.jsonl"
        mock = subprocess.Popen(
            [
                sys.executable,
                str(mock_script),
                "--contract",
                str(contract),
                "--log",
                str(log_path),
                "--task-id",
                task_id,
                "--submit-status",
                submit_status,
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            assert mock.stdout is not None
            ready_line = mock.stdout.readline()
            if not ready_line:
                stderr = mock.stderr.read() if mock.stderr is not None else ""
                fail(f"mock failed to start: {stderr}")
            base_url = json.loads(ready_line)["baseUrl"]
            env = os.environ.copy()
            env["VCF_INSTALLER_MOCK_URL"] = base_url + "/"
            completed = subprocess.run(
                [
                    "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    powershell_script(bundle_id, token, poll_interval),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=30,
            )
            if completed.returncode != 0:
                fail(
                    "PowerShell acceptance failed\n"
                    f"stdout:\n{completed.stdout}\n"
                    f"stderr:\n{completed.stderr}"
                )
            verify_metadata(read_marker(completed.stdout, "VCF_METADATA:"))
            verify_initializer_calls(read_marker(completed.stdout, "VCF_INITIALIZERS:"))
            sleeps = read_marker(completed.stdout, "VCF_SLEEPS:")
            if sleeps != expected_sleeps:
                fail(
                    "Start-Sleep must run once after each non-terminal task "
                    f"and never after the terminal task, got {sleeps!r}"
                )
            result = read_marker(completed.stdout, "VCF_RESULT:")
            if not isinstance(result, dict):
                fail(f"terminal result must be an object, got {result!r}")
            if result.get("id") != task_id or result.get("status") != "SUCCESSFUL":
                fail(f"function did not return the terminal task: {result}")
            verify_wire(
                read_log(log_path),
                bundle_id=bundle_id,
                task_id=task_id,
                token=token,
                expected_poll_count=expected_poll_count,
            )
        finally:
            mock.terminate()
            try:
                mock.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.wait(timeout=5)


def run_acceptance() -> None:
    nonce = secrets.token_hex(12)
    cases: list[
        tuple[str, int | None, list[dict[str, object]], str, int]
    ] = [
        ("default", None, [], "PENDING", 2),
        (
            "positive",
            POLL_INTERVAL,
            [
                {"parameterSet": "Milliseconds", "value": POLL_INTERVAL},
                {"parameterSet": "Milliseconds", "value": POLL_INTERVAL},
            ],
            "PENDING",
            2,
        ),
        ("immediate-terminal", POLL_INTERVAL, [], "SUCCESSFUL", 0),
    ]
    for (
        case_name,
        poll_interval,
        expected_sleeps,
        submit_status,
        expected_poll_count,
    ) in cases:
        run_acceptance_case(
            bundle_id=f"bundle {case_name}-{nonce}/async",
            task_id=f"task {case_name}-{nonce}/0101",
            token=f"token-{case_name}-{secrets.token_hex(18)}",
            poll_interval=poll_interval,
            expected_sleeps=expected_sleeps,
            submit_status=submit_status,
            expected_poll_count=expected_poll_count,
        )


def main() -> None:
    verify_contract()
    verify_source_shape()
    run_acceptance()
    print("VCF Installer asynchronous bundle download verification passed")


if __name__ == "__main__":
    main()
