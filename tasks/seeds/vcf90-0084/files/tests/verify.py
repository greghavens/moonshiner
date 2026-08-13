#!/usr/bin/env python3
"""Deterministic integration verifier for the VCF Operations for Logs module."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
MOCK = ROOT / "mock" / "vcf_logs_mock.py"
MANIFEST = ROOT / "src" / "Vcf.OperationsForLogs" / "Vcf.OperationsForLogs.psd1"
MODULE = ROOT / "src" / "Vcf.OperationsForLogs" / "Vcf.OperationsForLogs.psm1"

EXPECTED_TAG = "9.0.0.0"
EXPECTED_SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC_PATH = "specifications/vcf-operations/vcf-operations-for-logs-openapi.json"
EXPECTED_OPERATION_ID = "PUT_notification-webhook"
EXPECTED_PATH = "/api/v2/notification/webhook"
OPTIONAL_PROPERTIES = {
    "proxyId",
    "destinationApp",
    "contentType",
    "payload",
    "name",
    "headers",
    "acceptCert",
    "sendIndividualLogs",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_contract() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))

    require(contract["openapi"] == "3.0.1", "contract OpenAPI version changed")
    require(contract["title"] == "VCF Operations for Logs", "contract is not the 9.0 Logs spec")
    require(contract["basePath"] == "/api/v2", "contract base path changed")
    require(
        contract["securitySchemes"]["Bearer"]
        == {
            "type": "http",
            "description": "Authenticated requests must include an Authorization header with a session\nID that was retrieved from [`/api/v2/sessions`](#sessions).  The session ID\nhas a limited lifespan.  Access is allowed only to resources that the user is\nauthorized to use.\n",
            "name": "Authorization",
            "in": "header",
            "scheme": "Bearer",
        },
        "Bearer security scheme changed",
    )
    require(contract["source"]["tag"] == EXPECTED_TAG, "contract is not pinned to tag 9.0.0.0")
    require(contract["source"]["commitSha"] == EXPECTED_SHA, "contract commit SHA changed")
    require(contract["source"]["specPath"] == EXPECTED_SPEC_PATH, "contract spec path changed")
    require(set(contract["operations"]) == {EXPECTED_OPERATION_ID}, "contract must name one operation")

    operation = contract["operations"][EXPECTED_OPERATION_ID]
    require(operation["operationId"] == EXPECTED_OPERATION_ID, "operationId changed")
    require(operation["method"] == "PUT", "operation method must be PUT")
    require(operation["path"] == "/notification/webhook", "operation path changed")
    require(operation["request"]["contentType"] == "application/json", "request media type changed")
    require(
        set(operation["request"]["schema"]["properties"])
        == OPTIONAL_PROPERTIES | {"URLs"},
        "request property set does not match the selected 9.0 schema",
    )

    require(sources["tag"] == EXPECTED_TAG, "official source tag changed")
    require(sources["commitSha"] == EXPECTED_SHA, "official source commit changed")
    require(sources["specPath"] == EXPECTED_SPEC_PATH, "official source path changed")
    require(sources["operationIds"] == [EXPECTED_OPERATION_ID], "official operationIds changed")
    require("/9.0.0.0/" in sources["rawSpecUrl"], "raw source URL is not pinned to 9.0")
    require("9.1" not in SOURCES.read_text(encoding="utf-8"), "9.1 source must not be used")


def verify_module_shape() -> None:
    manifest_text = MANIFEST.read_text(encoding="utf-8")
    module_text = MODULE.read_text(encoding="utf-8")
    require("VMware.Sdk.Vcf.Ops" in manifest_text, "PowerCLI prerequisite is missing")
    require(
        "FunctionsToExport = @('Set-VcfOpsLogNotificationWebhook')" in manifest_text,
        "manifest does not export the required function",
    )
    require("Set-VcfOpsLogNotificationWebhook" in module_text, "required function is missing")

    vendored = [
        path
        for path in ROOT.rglob("*")
        if path.is_dir() and path.name.lower().startswith("vmware.sdk.vcf")
    ]
    require(not vendored, "VMware.Sdk.Vcf modules must not be vendored")


def wait_until_ready(process: subprocess.Popen[str], ready_path: Path) -> int:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f"mock exited early\nstdout: {stdout}\nstderr: {stderr}")
        if ready_path.exists():
            try:
                port = int(ready_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
            else:
                require(1 <= port <= 65535, "mock reported an invalid port")
                return port
        time.sleep(0.05)
    raise AssertionError("mock did not become ready")


def powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def invoke_module(
    port: int,
    script_path: Path,
    token: str,
    urls: list[str],
    full_body: dict[str, object],
) -> None:
    pwsh = shutil.which("pwsh")
    require(pwsh is not None, "pwsh is required")

    module_literal = str(MODULE).replace("'", "''")
    urls_literal = ", ".join(powershell_literal(item) for item in urls)
    script = f"""
$ErrorActionPreference = 'Stop'
Import-Module '{module_literal}' -Force
$command = Get-Command Set-VcfOpsLogNotificationWebhook -ErrorAction Stop
if ($command.CommandType -ne 'Function' -or $command.ModuleName -ne 'Vcf.OperationsForLogs') {{
    throw 'The function is not exported from the supplied module.'
}}
$arguments = @{{
    Server = [uri]'http://127.0.0.1:{port}'
    SessionId = {powershell_literal(token)}
    Urls = @({urls_literal})
}}
$first = Set-VcfOpsLogNotificationWebhook @arguments
$second = Set-VcfOpsLogNotificationWebhook @arguments
$fullArguments = @{{
    Server = [uri]'http://127.0.0.1:{port}'
    SessionId = {powershell_literal(token)}
    Urls = @({urls_literal})
    ProxyId = {powershell_literal(str(full_body['proxyId']))}
    DestinationApp = {powershell_literal(str(full_body['destinationApp']))}
    ContentType = {powershell_literal(str(full_body['contentType']))}
    Payload = {powershell_literal(str(full_body['payload']))}
    Name = {powershell_literal(str(full_body['name']))}
    WebhookHeaders = {powershell_literal(str(full_body['headers']))}
    AcceptCert = $false
    SendIndividualLogs = $true
}}
$third = Set-VcfOpsLogNotificationWebhook @fullArguments
$fourth = Set-VcfOpsLogNotificationWebhook @fullArguments
if (@($first.URLs).Count -ne 2 -or @($second.URLs).Count -ne 2 -or
    @($third.URLs).Count -ne 2 -or @($fourth.URLs).Count -ne 2) {{
    throw 'The function did not return the parsed API response.'
}}
foreach ($response in @($first, $second, $third, $fourth)) {{
    if ($response.URLs[0] -ne {powershell_literal(urls[1])} -or
        $response.URLs[1] -ne {powershell_literal(urls[0])}) {{
        throw 'The parsed API response did not preserve the returned URLs.'
    }}
}}
"""
    script_path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        env=os.environ.copy(),
    )
    require(
        completed.returncode == 0,
        f"PowerShell integration failed\nstdout: {completed.stdout}\nstderr: {completed.stderr}",
    )


def read_log(log_path: Path) -> list[dict]:
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]


def verify_wire(
    records: list[dict], token: str, urls: list[str], full_body: dict[str, object]
) -> None:
    require(len(records) == 4, f"expected exactly four PUTs, got {len(records)}")
    expected_bodies = [
        {"URLs": urls},
        {"URLs": urls},
        full_body,
        full_body,
    ]

    for index, (record, expected_body) in enumerate(zip(records, expected_bodies), start=1):
        require(record["operationId"] == EXPECTED_OPERATION_ID, f"request {index}: operationId changed")
        require(record["method"] == "PUT", f"request {index}: method must be PUT")
        require(record["rawTarget"] == EXPECTED_PATH, f"request {index}: path or query is incorrect")
        require(record["status"] == 200, f"request {index}: mock rejected the request")
        require(
            record["headers"].get("authorization") == f"Bearer {token}",
            f"request {index}: Authorization header is incorrect",
        )
        require(
            record["headers"].get("content-type") == "application/json",
            f"request {index}: Content-Type header is incorrect",
        )
        require(
            record["headers"].get("accept") == "application/json",
            f"request {index}: Accept header is incorrect",
        )
        require(record["jsonBody"] == expected_body, f"request {index}: JSON wire body is not exact")
        if index <= 2:
            require(
                set(record["jsonBody"]).isdisjoint(OPTIONAL_PROPERTIES),
                f"request {index}: unset optional fields were serialized",
            )
        else:
            require(
                set(record["jsonBody"]) == OPTIONAL_PROPERTIES | {"URLs"},
                f"request {index}: a bound property is missing or misnamed",
            )
        require(
            record["contentLength"] == len(record["rawBody"].encode("utf-8")),
            f"request {index}: Content-Length does not match wire bytes",
        )

    require(records[0]["mutated"] is True, "the first PUT did not apply the state")
    require(records[1]["mutated"] is False, "the repeated PUT duplicated the effect")
    require(records[0]["stateHash"] == records[1]["stateHash"], "retry changed resulting state")
    require(records[2]["mutated"] is True, "the full-property PUT did not apply the new state")
    require(records[3]["mutated"] is False, "the repeated full-property PUT duplicated the effect")
    require(records[2]["stateHash"] == records[3]["stateHash"], "full retry changed state")


def main() -> int:
    verify_contract()
    verify_module_shape()

    nonce = secrets.token_hex(12)
    token = f"fixture-session-{nonce}"
    urls = [
        f"https://alerts.example.com/{nonce}/primary",
        f"https://alerts.example.com/{nonce}/secondary",
    ]
    full_body: dict[str, object] = {
        "URLs": urls,
        "proxyId": f"proxy-{nonce}",
        "destinationApp": "custom",
        "contentType": "json",
        "payload": json.dumps(
            {"severity": "critical", "message": f"café-{nonce}"},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "name": f"Primary notifications {nonce}",
        "headers": f"X-Test-Run: {nonce}; X-Mode: strict",
        "acceptCert": False,
        "sendIndividualLogs": True,
    }

    with tempfile.TemporaryDirectory(prefix="vcf-logs-verify-") as temporary:
        temp_dir = Path(temporary)
        request_log = temp_dir / "requests.jsonl"
        ready_path = temp_dir / "ready.txt"
        script_path = temp_dir / "invoke.ps1"
        process = subprocess.Popen(
            [
                sys.executable,
                str(MOCK),
                "--contract",
                str(CONTRACT),
                "--request-log",
                str(request_log),
                "--ready-file",
                str(ready_path),
                "--port",
                "0",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            port = wait_until_ready(process, ready_path)
            invoke_module(port, script_path, token, urls, full_body)
            verify_wire(read_log(request_log), token, urls, full_body)
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    print("verification passed: exact 9.0 PUT bodies, omission, export, and retry-safe effects")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, OSError, subprocess.SubprocessError) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
