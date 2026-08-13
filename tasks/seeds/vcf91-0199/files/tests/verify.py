#!/usr/bin/env python3
"""Protected end-to-end verifier for the VCF Installer task inventory module."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from support.vcf_installer_mock import (
    ACCESS_TOKEN,
    EXPECTED_OPERATIONS,
    SDK_CONNECTION_OPERATION,
    SDK_DISCONNECT_OPERATION,
    ContractPinnedVcfInstaller,
    build_tasks,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
MANIFEST = ROOT / "src" / "VcfInstaller.TaskInventory" / "VcfInstaller.TaskInventory.psd1"
MODULE = ROOT / "src" / "VcfInstaller.TaskInventory" / "VcfInstaller.TaskInventory.psm1"
RUNNER = ROOT / "tests" / "invoke_submission.ps1"
COMMIT = "c3f3b52c845dd967cabbc21680e893292077d5ba"
SPEC_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def verify_contract() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    require(contract["source"]["commit"] == COMMIT, "contract commit was changed")
    require(contract["source"]["path"] == SPEC_PATH, "contract spec path was changed")
    require(contract["source"]["apiVersion"] == "9.1.0.0", "contract is not VCF 9.1")
    operations = {
        operation_id: (value["method"], value["path"])
        for operation_id, value in contract["operations"].items()
    }
    require(operations == EXPECTED_OPERATIONS, "contract operation set was changed")
    source_operations = {
        item["operationId"]: (item["method"], item["path"])
        for item in sources["operationIds"]
    }
    require(source_operations == EXPECTED_OPERATIONS, "official source operations do not match")
    require(sources["commit"] == COMMIT, "official source commit was changed")
    require(sources["specPath"] == SPEC_PATH, "official source path was changed")
    require(sources["repositoryLicense"] == "Apache-2.0", "source license is not recorded")
    require(
        sources["repository"] == "https://github.com/vmware/vcf-api-specs",
        "official repository was changed",
    )
    require(
        sources["specUrl"]
        == f"https://raw.githubusercontent.com/vmware/vcf-api-specs/{COMMIT}/{SPEC_PATH}",
        "official source URL is not pinned to the recorded commit",
    )
    require(
        {item["jsonPointer"] for item in sources["operationIds"]}
        == {
            "#/paths/~1v1~1tokens/post",
            "#/paths/~1v1~1tasks/get",
        },
        "official source JSON pointers were changed",
    )
    parameters = contract["operations"]["getTasks"]["parameters"]
    require(
        [item["name"] for item in parameters]
        == [
            "limit", "taskStatus", "taskType", "resourceId", "resourceType",
            "completedAfter", "pageNumber", "pageSize", "orderDirection",
            "orderBy", "taskName", "doLiveRefresh",
        ],
        "getTasks parameters no longer match the pinned specification",
    )
    require(all(item["required"] is False for item in parameters), "query parameters must be optional")
    require(
        contract["schemas"]["PageMetadata"]["properties"]["totalPages"]["format"] == "int32",
        "PageMetadata.totalPages contract is missing",
    )


def verify_submission_shape() -> None:
    manifest_text = MANIFEST.read_text(encoding="utf-8")
    require(
        re.search(r"RequiredModules[\s\S]*VMware\.Sdk\.Vcf\.Installer", manifest_text, re.I)
        is not None,
        "manifest must require VMware.Sdk.Vcf.Installer",
    )
    inspection_environment = os.environ.copy()
    inspection_environment.update(
        {
            "MOONSHINER_SOURCE_PATH": str(MODULE),
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
        }
    )
    inspection_script = r"""
$parseErrors = $null
$tokens = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $env:MOONSHINER_SOURCE_PATH,
    [ref] $tokens,
    [ref] $parseErrors
)
$commands = @(
    $ast.FindAll(
        { param($node) $node -is [Management.Automation.Language.CommandAst] },
        $true
    ) | ForEach-Object { $_.GetCommandName() } | Where-Object { $_ }
)
$types = @(
    $ast.FindAll(
        { param($node) $node -is [Management.Automation.Language.TypeExpressionAst] },
        $true
    ) | ForEach-Object { $_.TypeName.FullName }
)
$strings = @(
    $ast.FindAll(
        { param($node) $node -is [Management.Automation.Language.StringConstantExpressionAst] },
        $true
    ) | ForEach-Object { $_.Value }
)
$result = [ordered]@{
    commands = $commands
    types = $types
    strings = $strings
    errorCount = @($parseErrors).Count
}
ConvertTo-Json -InputObject $result -Compress
"""
    inspection = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", inspection_script],
        cwd=ROOT,
        env=inspection_environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    require(
        inspection.returncode == 0,
        f"could not inspect PowerShell syntax: {inspection.stderr[-500:]}",
    )
    inspection_lines = [line for line in inspection.stdout.splitlines() if line.strip()]
    require(inspection_lines, "PowerShell syntax inspection returned no result")
    ast_shape = json.loads(inspection_lines[-1])
    require(ast_shape["errorCount"] == 0, "submission module has PowerShell parse errors")
    def ast_values(name: str) -> list[str]:
        values = ast_shape[name]
        return [values] if isinstance(values, str) else values

    command_names = {name.casefold() for name in ast_values("commands")}
    require(
        "invoke-vcfinstallergettasks" in command_names,
        "implementation must invoke Invoke-VcfInstallerGetTasks",
    )
    banned_commands = {
        "invoke-restmethod", "invoke-webrequest", "curl", "curl.exe", "wget", "wget.exe",
    }
    found_commands = sorted(command_names & banned_commands)
    type_names = {name.casefold() for name in ast_values("types")}
    banned_type_suffixes = (
        "webclient", "httpclient", "httpwebrequest", "webrequest", "tcpclient", "udpclient",
    )
    found_types = sorted(
        name for name in type_names if name.endswith(banned_type_suffixes)
    )
    string_values = {value.casefold() for value in ast_values("strings")}
    found_tools = sorted(string_values & {"curl", "curl.exe", "wget", "wget.exe"})
    found = found_commands + found_types + found_tools
    require(not found, f"direct HTTP client is not allowed: {', '.join(found)}")


def create_certificate(directory: Path) -> tuple[Path, Path]:
    cert = directory / "loopback-cert.pem"
    key = directory / "loopback-key.pem"
    command = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
        "-days", "1", "-subj", "/CN=127.0.0.1", "-addext",
        "subjectAltName=IP:127.0.0.1", "-addext",
        "basicConstraints=critical,CA:TRUE", "-keyout", str(key), "-out", str(cert),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    require(result.returncode == 0, f"could not create loopback TLS certificate: {result.stderr[-400:]}")
    return cert, key


def run_submission(port: int, cert: Path, output: Path) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "SSL_CERT_FILE": str(cert),
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "VMWARE_CEIP_ENABLED": "0",
        }
    )
    command = [
        "pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(RUNNER),
        "-ManifestPath", str(MANIFEST), "-Port", str(port), "-OutputPath", str(output),
    ]
    result = subprocess.run(
        command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120
    )
    detail = (result.stdout + "\n" + result.stderr).strip()
    require(result.returncode == 0, f"PowerShell integration failed:\n{detail[-2000:]}")
    require(output.exists(), "PowerShell command did not emit its result")


def verify_output(output: Path) -> None:
    actual = json.loads(output.read_text(encoding="utf-8"))
    expected = sorted(
        (
            {
                "id": item["id"],
                "creationTimestamp": item["creationTimestamp"],
                "status": item["status"],
            }
            for item in build_tasks()
        ),
        key=lambda item: (item["creationTimestamp"], item["id"]),
    )
    require(actual == expected, "result is incomplete, duplicated, or not in the required stable order")


def verify_wire(requests: list[dict[str, object]]) -> None:
    require(requests, "loopback service received no requests")
    allowed_operations = {
        *EXPECTED_OPERATIONS,
        SDK_CONNECTION_OPERATION[0],
        SDK_DISCONNECT_OPERATION[0],
    }
    require(
        all(item["operationId"] in allowed_operations for item in requests),
        "client called an unexpected loopback operation",
    )
    token_requests = [item for item in requests if item["operationId"] == "createToken"]
    require(len(token_requests) == 1, "connection must create exactly one token")
    token = token_requests[0]
    require(token["method"] == "POST" and token["path"] == "/v1/tokens", "token wire target is wrong")
    require(token["query"] == {}, "createToken must not have a query string")
    token_body = json.loads(token["body"])
    require(
        token_body == {"username": "seed-user", "password": "seed-password"},
        "createToken body must contain only the bound username and password",
    )

    probe_requests = [
        item for item in requests if item["operationId"] == SDK_CONNECTION_OPERATION[0]
    ]
    require(len(probe_requests) == 1, "connection must perform exactly one SDK version probe")
    probe = probe_requests[0]
    require(
        probe["method"] == SDK_CONNECTION_OPERATION[1]
        and probe["path"] == SDK_CONNECTION_OPERATION[2]
        and probe["query"] == {}
        and probe["body"] == "",
        "SDK connection probe wire shape is wrong",
    )
    require(
        probe["headers"].get("authorization") == f"Bearer {ACCESS_TOKEN}",
        "SDK connection probe bearer token is missing",
    )

    disconnect_requests = [
        item for item in requests if item["operationId"] == SDK_DISCONNECT_OPERATION[0]
    ]
    require(len(disconnect_requests) == 1, "connection must perform exactly one SDK disconnect")
    disconnect = disconnect_requests[0]
    require(
        disconnect["method"] == SDK_DISCONNECT_OPERATION[1]
        and disconnect["path"] == SDK_DISCONNECT_OPERATION[2]
        and disconnect["query"] == {},
        "SDK disconnect wire target is wrong",
    )
    require(
        json.loads(disconnect["body"]) == "loopback-refresh-token",
        "SDK disconnect refresh token body is wrong",
    )
    require(
        disconnect["headers"].get("authorization") == f"Bearer {ACCESS_TOKEN}",
        "SDK disconnect bearer token is missing",
    )

    task_requests = [item for item in requests if item["operationId"] == "getTasks"]
    require(len(task_requests) == 7, "getTasks pagination/filter request count is wrong")
    require(
        [item["query"] for item in task_requests[:3]]
        == [
            {"pageNumber": ["0"], "pageSize": ["3"], "taskStatus": ["FAILED"]},
            {"pageNumber": ["1"], "pageSize": ["3"], "taskStatus": ["FAILED"]},
            {"pageNumber": ["2"], "pageSize": ["3"], "taskStatus": ["FAILED"]},
        ],
        "getTasks query shape is wrong or an unset optional field was serialized",
    )
    require(
        task_requests[3]["query"]
        == {
            "pageNumber": ["0"],
            "pageSize": ["100"],
            "taskStatus": ["FAILED"],
            "taskType": ["HOST_COMMISSION"],
            "resourceId": ["resource-42"],
            "resourceType": ["HOST"],
            "completedAfter": ["0"],
            "taskName": ["Contract"],
            "doLiveRefresh": ["false"],
        },
        "bound optional filters were not forwarded with their exact values",
    )
    require(
        task_requests[4]["query"]
        == {"pageNumber": ["0"], "pageSize": ["100"], "taskStatus": ["FAILED"]},
        "the default page size was not sent as 100",
    )
    require(
        [item["query"] for item in task_requests[5:]]
        == [
            {
                "pageNumber": ["0"],
                "pageSize": ["3"],
                "taskName": ["MOONSHINER_MISSING_METADATA"],
            },
            {
                "pageNumber": ["0"],
                "pageSize": ["3"],
                "taskName": ["MOONSHINER_WRONG_PAGE"],
            },
        ],
        "malformed pagination checks did not use exact SDK query parameters",
    )
    for item in task_requests:
        require(item["method"] == "GET" and item["path"] == "/v1/tasks", "getTasks target is wrong")
        require(item["body"] == "", "GET /v1/tasks must not have a request body")
        authorization = item["headers"].get("authorization")
        require(authorization == f"Bearer {ACCESS_TOKEN}", "getTasks bearer token is missing")
    require(len(requests) == 10, "client made unexpected duplicate loopback requests")


def main() -> int:
    try:
        verify_contract()
        verify_submission_shape()
        with tempfile.TemporaryDirectory(prefix="vcf91-0199-") as temporary:
            directory = Path(temporary)
            cert, key = create_certificate(directory)
            request_log = directory / "requests.jsonl"
            output = directory / "result.json"
            with ContractPinnedVcfInstaller(CONTRACT, request_log, cert, key) as mock:
                run_submission(mock.port, cert, output)
                verify_output(output)
                verify_wire(mock.requests())
        print("PASS: VCF Installer pages, stable ordering, SDK usage, and exact wire shape verified")
        return 0
    except (VerificationError, KeyError, ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
