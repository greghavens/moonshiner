#!/usr/bin/env python3
"""Deterministic verifier for the resilient VCF Installer module."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MODULE_DIR = ROOT / "src" / "VcfInstaller.Resilient"
MANIFEST_PATH = MODULE_DIR / "VcfInstaller.Resilient.psd1"
IMPLEMENTATION_PATH = MODULE_DIR / "VcfInstaller.Resilient.psm1"
MOCK_PATH = ROOT / "mock" / "vcf_installer_mock.py"

EXPECTED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC_PATH = "specifications/vcf-installer/vcf-installer-openapi.json"
EXPECTED_OPERATIONS = {
    "createToken": ("POST", "/v1/tokens"),
    "startBundleDownloadByID": ("PATCH", "/v1/bundles/{id}"),
    "getTask": ("GET", "/v1/tasks/{id}"),
    "refreshAccessToken": ("PATCH", "/v1/tokens/access-token/refresh"),
}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_source_record() -> None:
    sources = load_json(SOURCES_PATH)
    require(sources.get("tag") == "9.0.0.0", "official source tag must be 9.0.0.0")
    require(sources.get("tagCommitSha") == EXPECTED_SHA, "incorrect tag commit SHA")
    require(sources.get("specPath") == EXPECTED_SPEC_PATH, "incorrect spec path")
    require(sources.get("license") == "Apache-2.0", "incorrect source license")
    records = sources.get("operations")
    require(isinstance(records, list), "official source operations must be a list")
    by_id = {record.get("operationId"): record for record in records}
    require(set(by_id) == set(EXPECTED_OPERATIONS), "official source operationIds differ")
    for operation_id, record in by_id.items():
        require(record.get("tag") == "9.0.0.0", f"wrong tag for {operation_id}")
        require(record.get("commitSha") == EXPECTED_SHA, f"wrong SHA for {operation_id}")
        require(
            record.get("specPath") == EXPECTED_SPEC_PATH,
            f"wrong spec path for {operation_id}",
        )


def verify_contract() -> None:
    contract = load_json(CONTRACT_PATH)
    require(contract.get("apiVersion") == "9.0.0.0", "contract is not VCF 9.0.0.0")
    source = contract.get("source", {})
    require(source.get("tag") == "9.0.0.0", "contract source tag differs")
    require(source.get("commitSha") == EXPECTED_SHA, "contract source SHA differs")
    require(source.get("specPath") == EXPECTED_SPEC_PATH, "contract spec path differs")

    operations = contract.get("operations")
    require(isinstance(operations, list), "contract operations must be a list")
    actual = {
        operation.get("operationId"): (operation.get("method"), operation.get("path"))
        for operation in operations
    }
    require(actual == EXPECTED_OPERATIONS, "contract operation set or routes differ")
    by_id = {operation["operationId"]: operation for operation in operations}

    create_request = by_id["createToken"]["request"]
    require(create_request["mediaType"] == "application/json", "createToken media type")
    require(create_request["schema"] == "TokenCreationSpec", "createToken schema")
    refresh_request = by_id["refreshAccessToken"]["request"]
    require(refresh_request["mediaType"] == "application/json", "refresh media type")
    require(refresh_request["schema"]["type"] == "string", "refresh body is not string")
    bundle_request = by_id["startBundleDownloadByID"]["request"]
    require(bundle_request["schema"] == "BundleUpdateSpec", "bundle request schema")

    schemas = contract.get("schemas", {})
    token_spec = schemas.get("TokenCreationSpec", {})
    require(token_spec.get("required") == [], "TokenCreationSpec required list differs")
    require(
        set(token_spec.get("properties", {}))
        == {"username", "password", "apiKey", "idToken"},
        "TokenCreationSpec properties differ",
    )
    download_spec = schemas.get("BundleDownloadSpec", {})
    require(download_spec.get("required") == [], "BundleDownloadSpec required list differs")
    require(
        set(download_spec.get("properties", {}))
        == {"scheduledTimestamp", "downloadNow", "cancelNow"},
        "BundleDownloadSpec properties differ",
    )


def verify_module_shape() -> None:
    manifest = MANIFEST_PATH.read_text(encoding="utf-8")
    implementation = IMPLEMENTATION_PATH.read_text(encoding="utf-8")
    require(
        "VMware.Sdk.Vcf.Installer" in manifest,
        "module manifest must require VMware.Sdk.Vcf.Installer",
    )
    require(
        "RequiredVersion = '13.4.0.24798382'" in manifest,
        "module manifest must pin VMware.Sdk.Vcf.Installer 13.4.0.24798382",
    )
    for initializer in (
        "Initialize-VcfInstallerTokenCreationSpec",
        "Initialize-VcfInstallerBundleDownloadSpec",
        "Initialize-VcfInstallerBundleUpdateSpec",
    ):
        require(initializer in implementation, f"implementation does not use {initializer}")
        require(
            re.search(
                rf"(?im)^\s*function\s+(?:global:|script:|local:)?{re.escape(initializer)}\b",
                implementation,
            )
            is None,
            f"implementation must not redefine {initializer}",
        )
    require(
        "Start-VcfInstallerBundleDownload" in implementation,
        "public function is missing",
    )

    vendored = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part.lower().startswith("vmware.sdk.vcf") for part in relative.parts):
            vendored.append(str(relative))
    require(not vendored, f"VMware SDK dependency was vendored: {vendored}")


def wait_for_ready(ready_path: Path, process: subprocess.Popen[str]) -> str:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if ready_path.exists():
            return load_json(ready_path)["baseUri"]
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise VerificationError(
                f"mock exited before ready (code {process.returncode})\n{stdout}\n{stderr}"
            )
        time.sleep(0.02)
    raise VerificationError("mock did not become ready")


def invoke_solution(
    base_uri: str,
    output_path: Path,
    runner_path: Path,
    username: str,
    password: str,
    bundle_id: str,
    terminal_status: str,
) -> dict[str, Any]:
    runner = r'''param(
    [Parameter(Mandatory)][string] $ModuleManifest,
    [Parameter(Mandatory)][uri] $BaseUri,
    [Parameter(Mandatory)][string] $OutputPath,
    [Parameter(Mandatory)][string] $Username,
    [Parameter(Mandatory)][string] $Password,
    [Parameter(Mandatory)][string] $BundleId
)
$ErrorActionPreference = 'Stop'
Import-Module $ModuleManifest -Force
$global:VcfInitializerCallCounts = @{
    Token = 0
    Download = 0
    Update = 0
}
$breakpoints = @(
    Set-PSBreakpoint -Command 'Initialize-VcfInstallerTokenCreationSpec' -Action {
        $global:VcfInitializerCallCounts.Token++
    }
    Set-PSBreakpoint -Command 'Initialize-VcfInstallerBundleDownloadSpec' -Action {
        $global:VcfInitializerCallCounts.Download++
    }
    Set-PSBreakpoint -Command 'Initialize-VcfInstallerBundleUpdateSpec' -Action {
        $global:VcfInitializerCallCounts.Update++
    }
)
$securePassword = ConvertTo-SecureString $Password -AsPlainText -Force
$credential = [pscredential]::new($Username, $securePassword)
try {
    $result = Start-VcfInstallerBundleDownload `
        -BaseUri $BaseUri `
        -Credential $credential `
        -BundleId $BundleId `
        -PollIntervalMilliseconds 0
}
finally {
    $breakpoints | Remove-PSBreakpoint
}
foreach ($name in @('Token', 'Download', 'Update')) {
    if ($global:VcfInitializerCallCounts[$name] -lt 1) {
        throw "Generated model initializer $name was not called."
    }
}
$result | ConvertTo-Json -Depth 20 -Compress | Set-Content -LiteralPath $OutputPath -Encoding utf8NoBOM
'''
    runner_path.write_text(runner, encoding="utf-8")
    environment = os.environ.copy()
    environment["POWERSHELL_TELEMETRY_OPTOUT"] = "1"
    completed = subprocess.run(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(runner_path),
            "-ModuleManifest",
            str(MANIFEST_PATH),
            "-BaseUri",
            base_uri,
            "-OutputPath",
            str(output_path),
            "-Username",
            username,
            "-Password",
            password,
            "-BundleId",
            bundle_id,
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    require(
        completed.returncode == 0,
        "PowerShell scenario failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}",
    )
    require(output_path.exists(), "PowerShell scenario produced no result")
    result = load_json(output_path)
    require(isinstance(result.get("id"), str) and result["id"], "function returned no task id")
    require(
        result.get("status") == terminal_status,
        f"function did not return terminal status {terminal_status}",
    )
    return result


def content_type(record: dict[str, Any]) -> str | None:
    value = record["headers"].get("content-type")
    if value is None:
        return None
    return value.strip().lower()


def assert_json_body(record: dict[str, Any], expected: Any, label: str) -> None:
    require(record["bodyLength"] > 0, f"{label} body is empty")
    try:
        actual = json.loads(record["body"])
    except json.JSONDecodeError as error:
        raise VerificationError(f"{label} body is not JSON: {error}") from error
    require(actual == expected, f"{label} body differs: {actual!r}")


def verify_request_log(
    log_path: Path,
    username: str,
    password: str,
    bundle_id: str,
    task_id: str,
) -> None:
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    expected_routes = [
        ("POST", "/v1/tokens"),
        ("PATCH", f"/v1/bundles/{quote(bundle_id, safe='')}"),
        ("GET", f"/v1/tasks/{task_id}"),
        ("PATCH", "/v1/tokens/access-token/refresh"),
        ("GET", f"/v1/tasks/{task_id}"),
        ("GET", f"/v1/tasks/{task_id}"),
    ]
    actual_routes = [(record["method"], record["path"]) for record in records]
    require(actual_routes == expected_routes, f"request operation sequence differs: {actual_routes}")
    require(all(record["query"] == "" for record in records), "unexpected query string")
    require(
        all(record["headers"].get("accept") == "application/json" for record in records),
        "every operation must send Accept: application/json",
    )

    create, start, expired_poll, refresh, resumed_poll, final_poll = records
    require(content_type(create) == "application/json", "createToken content type differs")
    assert_json_body(
        create,
        {"username": username, "password": password},
        "createToken",
    )
    create_document = json.loads(create["body"])
    require("apiKey" not in create_document, "unset apiKey must be omitted")
    require("idToken" not in create_document, "unset idToken must be omitted")
    require("authorization" not in create["headers"], "createToken must not send bearer auth")

    require(content_type(start) == "application/json", "bundle start content type differs")
    assert_json_body(start, {"bundleDownloadSpec": {"downloadNow": True}}, "bundle start")
    bundle_document = json.loads(start["body"])["bundleDownloadSpec"]
    require(
        "scheduledTimestamp" not in bundle_document,
        "unset scheduledTimestamp must be omitted",
    )
    require("cancelNow" not in bundle_document, "unset cancelNow must be omitted")
    initial_authorization = start["headers"].get("authorization")
    require(
        isinstance(initial_authorization, str) and initial_authorization.startswith("Bearer "),
        "bundle start bearer header is missing",
    )

    require(expired_poll["headers"].get("authorization") == initial_authorization, "first poll token differs")
    require(expired_poll["bodyLength"] == 0, "GET task must not send a body")
    require(content_type(expired_poll) is None, "GET task must not send Content-Type")

    require(content_type(refresh) == "application/json", "refresh content type differs")
    refresh_document = json.loads(refresh["body"])
    require(isinstance(refresh_document, str) and refresh_document, "refresh body must be a JSON string")
    require("authorization" not in refresh["headers"], "refresh must not send stale bearer auth")

    refreshed_authorization = resumed_poll["headers"].get("authorization")
    require(
        isinstance(refreshed_authorization, str)
        and refreshed_authorization.startswith("Bearer ")
        and refreshed_authorization != initial_authorization,
        "polling did not switch to the refreshed bearer token",
    )
    require(
        final_poll["headers"].get("authorization") == refreshed_authorization,
        "final poll did not preserve refreshed token",
    )
    for record in (resumed_poll, final_poll):
        require(record["bodyLength"] == 0, "GET task must not send a body")
        require(content_type(record) is None, "GET task must not send Content-Type")

    bundle_submissions = [
        record
        for record in records
        if record["method"] == "PATCH" and record["path"].startswith("/v1/bundles/")
    ]
    require(len(bundle_submissions) == 1, "bundle download was submitted more than once")


def verify_integration() -> None:
    scenario_digest = hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()[:12]
    username = f"operator-{scenario_digest}@vsphere.local"
    password = f"Fixture-{scenario_digest}!"
    terminal_statuses = (
        "SUCCESSFUL",
        "FAILED",
        "CANCELLED",
        "COMPLETED_WITH_WARNING",
        "SKIPPED",
    )
    with tempfile.TemporaryDirectory(prefix="vcf90-verifier-") as temporary:
        temp = Path(temporary)
        for index, terminal_status in enumerate(terminal_statuses):
            label = terminal_status.lower()
            bundle_id = f"bundle/{scenario_digest}?variant={label}"
            request_log = temp / f"requests-{index}.ndjson"
            ready_file = temp / f"ready-{index}.json"
            output_file = temp / f"result-{index}.json"
            runner_file = temp / f"invoke-{index}.ps1"
            mock = subprocess.Popen(
                [
                    sys.executable,
                    str(MOCK_PATH),
                    "--contract",
                    str(CONTRACT_PATH),
                    "--request-log",
                    str(request_log),
                    "--ready-file",
                    str(ready_file),
                    "--terminal-status",
                    terminal_status,
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                base_uri = wait_for_ready(ready_file, mock)
                result = invoke_solution(
                    base_uri,
                    output_file,
                    runner_file,
                    username,
                    password,
                    bundle_id,
                    terminal_status,
                )
                verify_request_log(
                    request_log,
                    username,
                    password,
                    bundle_id,
                    result["id"],
                )
            finally:
                mock.terminate()
                try:
                    mock.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    mock.kill()
                    mock.communicate(timeout=3)


def main() -> int:
    try:
        verify_source_record()
        verify_contract()
        verify_module_shape()
        verify_integration()
    except (VerificationError, OSError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: VCF Installer token refresh resumes the original bundle task")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
