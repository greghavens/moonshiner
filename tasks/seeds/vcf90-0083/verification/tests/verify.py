#!/usr/bin/env python3
"""Protected deterministic verification for the VCF Logs module task."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import parse_qsl, urlsplit


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "contract.json"
SOURCES = ROOT / "docs" / "official_sources.json"
MODULE_DIR = ROOT / "src" / "Vcf.OperationsForLogs"
MANIFEST = MODULE_DIR / "Vcf.OperationsForLogs.psd1"
IMPLEMENTATION = MODULE_DIR / "Vcf.OperationsForLogs.psm1"
EXPECTED_COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
EXPECTED_SPEC = "specifications/vcf-operations/vcf-operations-for-logs-openapi.json"
EXPECTED_OPERATION = "GET_events-+path"
EXPECTED_CONTRACT_SHA256 = "d2069e15e7aac5f20c574690720ee007cda577ff0a476df742d423a81bf95acb"
EXPECTED_FIXTURE_SHA256 = {
    "docs/official_sources.json": "6b7fd5ff07dd56b8cdb379efcfd92ff54e1e8c459b1358d49399bf44a98b5f55",
    "mock/events.json": "565bce7d1b080036b9ab8f6bd452963a2a4d7371326847ab3271da107f802caa",
    "mock/server.py": "e1741562755d5b30831cda5796ca201459c945ff1ba48d321b7315448a1f8a0e",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **kwargs,
    )
    if result.returncode != 0:
        fail(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def verify_contract_pin() -> None:
    raw = CONTRACT.read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != EXPECTED_CONTRACT_SHA256:
        fail(f"docs/contract.json changed: {actual_hash}")
    for relative_path, expected_hash in EXPECTED_FIXTURE_SHA256.items():
        actual_hash = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            fail(f"protected fixture changed: {relative_path}")

    contract = json.loads(raw)
    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    if contract["source"] != {
        "tag": "9.0.0.0",
        "commitSha": EXPECTED_COMMIT,
        "specPath": EXPECTED_SPEC,
    }:
        fail("contract source is not pinned to the VCF 9.0 specification")
    if list(contract["operations"]) != [EXPECTED_OPERATION]:
        fail("contract must contain exactly GET_events-+path")
    operation = contract["operations"][EXPECTED_OPERATION]
    if (operation["method"], operation["path"]) != ("GET", "/events/{+path}"):
        fail("events operation method or path does not match the 9.0 specification")
    parameter_names = [item["name"] for item in operation["parameters"]]
    if parameter_names != [
        "+path",
        "limit",
        "timeout",
        "view",
        "content-pack-fields",
        "order-by-direction",
    ]:
        fail("events parameter contract changed")
    expected_sources = {
        "repository": "https://github.com/vmware/vcf-api-specs",
        "license": "Apache-2.0",
        "tag": "9.0.0.0",
        "commitSha": EXPECTED_COMMIT,
        "specPath": EXPECTED_SPEC,
        "specUrl": (
            "https://raw.githubusercontent.com/vmware/vcf-api-specs/"
            f"{EXPECTED_COMMIT}/{EXPECTED_SPEC}"
        ),
        "operationIds": [EXPECTED_OPERATION],
    }
    if sources != expected_sources:
        fail("official source record is not the pinned VCF 9.0 source record")


def verify_module_shape(pwsh: str) -> None:
    if not MANIFEST.is_file() or not IMPLEMENTATION.is_file():
        fail("both required PowerShell module files must exist")

    unexpected = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and "VMware.Sdk.Vcf" in path.name
        and path.parent != MODULE_DIR
    ]
    if unexpected:
        fail(f"VMware.Sdk.Vcf prerequisites must not be vendored: {unexpected}")

    script = r"""
$ErrorActionPreference = 'Stop'
$manifest = Import-PowerShellDataFile -LiteralPath $env:VCF_MANIFEST
if ($manifest.RootModule -ne 'Vcf.OperationsForLogs.psm1') { throw 'wrong RootModule' }
if (@($manifest.FunctionsToExport).Count -ne 1 -or $manifest.FunctionsToExport -ne 'Get-VcfLogEvent') { throw 'wrong exports' }
foreach ($key in @('CmdletsToExport', 'VariablesToExport', 'AliasesToExport')) {
    if (@($manifest[$key]).Count -ne 0) { throw "$key must be empty" }
}
$requiredNames = @($manifest.RequiredModules | ForEach-Object {
    if ($_ -is [string]) { $_ } elseif ($_.ModuleName) { $_.ModuleName } else { [string]$_ }
})
if ($requiredNames.Count -ne 1 -or $requiredNames[0] -ne 'VMware.Sdk.Vcf.Ops') { throw 'wrong required modules' }
$tokens = $null
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile($env:VCF_IMPLEMENTATION, [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count -ne 0) { throw ($errors | Out-String) }
$module = Import-Module -Name $env:VCF_IMPLEMENTATION -Force -PassThru
$exports = @(Get-Command -Module $module.Name -CommandType Function)
if ($exports.Count -ne 1 -or $exports[0].Name -ne 'Get-VcfLogEvent') { throw 'implementation exports the wrong functions' }
$command = $exports[0]
if (-not $command.CmdletBinding) { throw 'Get-VcfLogEvent is not an advanced function' }
foreach ($name in @('ServerUri', 'SessionId', 'StartTimestamp', 'EndTimestamp')) {
    $mandatory = @($command.Parameters[$name].Attributes | Where-Object {
        $_ -is [System.Management.Automation.ParameterAttribute] -and $_.Mandatory
    })
    if ($mandatory.Count -eq 0) { throw "$name is not mandatory" }
}
foreach ($name in @('PageSize', 'Timeout', 'View', 'ContentPackFields')) {
    if (-not $command.Parameters.ContainsKey($name)) { throw "$name is missing" }
    $mandatory = @($command.Parameters[$name].Attributes | Where-Object {
        $_ -is [System.Management.Automation.ParameterAttribute] -and $_.Mandatory
    })
    if ($mandatory.Count -ne 0) { throw "$name must be optional" }
}
if ($command.Parameters['ServerUri'].ParameterType -ne [Uri]) { throw 'ServerUri has the wrong type' }
if ($command.Parameters['SessionId'].ParameterType -ne [string]) { throw 'SessionId has the wrong type' }
if ($command.Parameters['StartTimestamp'].ParameterType -ne [long]) { throw 'StartTimestamp has the wrong type' }
if ($command.Parameters['EndTimestamp'].ParameterType -ne [long]) { throw 'EndTimestamp has the wrong type' }
if ($command.Parameters['PageSize'].ParameterType -ne [int]) { throw 'PageSize has the wrong type' }
"""
    environment = os.environ.copy()
    environment["VCF_MANIFEST"] = str(MANIFEST)
    environment["VCF_IMPLEMENTATION"] = str(IMPLEMENTATION)
    run([pwsh, "-NoLogo", "-NoProfile", "-Command", script], env=environment)


def wait_for_ready(path: Path, process: subprocess.Popen[str]) -> dict[str, object]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            fail(f"mock exited before readiness\nstdout:\n{stdout}\nstderr:\n{stderr}")
        time.sleep(0.02)
    fail("mock did not become ready")
    return {}


def canonical_instant(value: object) -> object:
    """An ISO-8601 instant reduced to the instant, not to how it was written."""
    if not isinstance(value, str):
        return value
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return value
    if moment.tzinfo is None:
        return value
    return moment.astimezone(timezone.utc).isoformat()


def canonical_events(events: object) -> object:
    """The event collection, with each `timestampString` read as an instant.

    PowerShell's JSON reader turns any string shaped like an ISO-8601 instant
    into a `[datetime]` on the way in, and its writer prints one back with only
    the precision the value needs -- a trailing `.000` cannot survive the round
    trip, and no switch turns the conversion off. Comparing the printed forms
    would judge the JSON reader that shipped with the shell rather than the
    collection the module returned. Everything else, including which events are
    present and the order they arrive in, is still compared exactly.
    """
    if not isinstance(events, list):
        return events
    canonical = []
    for event in events:
        if not isinstance(event, dict) or "timestampString" not in event:
            canonical.append(event)
            continue
        reduced = dict(event)
        reduced["timestampString"] = canonical_instant(event["timestampString"])
        canonical.append(reduced)
    return canonical


def verify_behavior(pwsh: str) -> None:
    events = json.loads((ROOT / "mock" / "events.json").read_text(encoding="utf-8"))
    expected_events = canonical_events(
        sorted(events, key=lambda event: (int(event["timestamp"]), event["text"]))
    )

    with tempfile.TemporaryDirectory(prefix="vcf-logs-verifier-") as temp_name:
        temp = Path(temp_name)
        request_log = temp / "requests.ndjson"
        ready_file = temp / "ready.json"
        server = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "mock" / "server.py"),
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
            base_uri = f"http://{ready['host']}:{ready['port']}"
            script = r"""
$ErrorActionPreference = 'Stop'
Import-Module -Name $env:VCF_IMPLEMENTATION -Force
$events = @(Get-VcfLogEvent `
    -ServerUri ([Uri]$env:VCF_BASE_URI) `
    -SessionId 'fixture-session' `
    -StartTimestamp 1700000000000 `
    -EndTimestamp 1700000009999 `
    -PageSize 2)
$simpleEvents = @(Get-VcfLogEvent `
    -ServerUri ([Uri]$env:VCF_BASE_URI) `
    -SessionId 'fixture-session' `
    -StartTimestamp 1700000000000 `
    -EndTimestamp 1700000009999 `
    -Timeout 0 `
    -View SIMPLE `
    -ContentPackFields 'source field')
[pscustomobject]@{
    events = $events
    simpleEvents = $simpleEvents
} | ConvertTo-Json -Depth 12 -Compress
"""
            environment = os.environ.copy()
            environment["VCF_IMPLEMENTATION"] = str(IMPLEMENTATION)
            environment["VCF_BASE_URI"] = base_uri
            result = run(
                [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                env=environment,
                timeout=20,
            )
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)

        output_lines = [line for line in result.stdout.splitlines() if line.strip()]
        if not output_lines:
            fail("Get-VcfLogEvent produced no JSON output")
        actual = json.loads(output_lines[-1])
        actual_events = canonical_events(actual.get("events"))
        if actual_events != expected_events:
            fail(
                "event output is incomplete or not stably ordered\n"
                f"expected: {json.dumps(expected_events)}\n"
                f"actual:   {json.dumps(actual_events)}"
            )
        if canonical_events(actual.get("simpleEvents")) != expected_events:
            fail("SIMPLE view did not return the complete stable event collection")

        requests = [
            json.loads(line)
            for line in request_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        expected_requests = [
            (
                "/api/v2/events/timestamp/%3E%3D1700000000000/timestamp/%3C%3D1700000009999",
                [("limit", "2"), ("order-by-direction", "DESC")],
            ),
            (
                "/api/v2/events/timestamp/%3E%3D1700000000000/timestamp/%3C%3D1700000003999",
                [("limit", "2"), ("order-by-direction", "DESC")],
            ),
            (
                "/api/v2/events/timestamp/%3E%3D1700000000000/timestamp/%3C%3D1700000002999",
                [("limit", "2"), ("order-by-direction", "DESC")],
            ),
            (
                "/api/v2/events/timestamp/%3E%3D1700000000000/timestamp/%3C%3D1700000000999",
                [("limit", "2"), ("order-by-direction", "DESC")],
            ),
            (
                "/api/v2/events/timestamp/%3E%3D1700000000000/timestamp/%3C%3D1700000009999",
                [
                    ("limit", "100"),
                    ("order-by-direction", "DESC"),
                    ("timeout", "0"),
                    ("view", "SIMPLE"),
                    ("content-pack-fields", "source field"),
                ],
            ),
        ]
        actual_targets = [
            (urlsplit(request["target"]).path, parse_qsl(urlsplit(request["target"]).query))
            for request in requests
        ]
        if len(actual_targets) != len(expected_requests) or any(
            actual_path != expected_path or sorted(actual_query) != sorted(expected_query)
            for (actual_path, actual_query), (expected_path, expected_query) in zip(
                actual_targets, expected_requests
            )
        ):
            fail(
                "request targets do not match the required keyset wire shape\n"
                f"actual: {json.dumps(requests, indent=2)}"
            )
        for request in requests:
            if request != {
                "method": "GET",
                "target": request["target"],
                "authorization": "Bearer fixture-session",
                "contentType": None,
                "contentLength": None,
                "bodyLength": 0,
            }:
                fail(f"unexpected request method, headers, or body: {request}")


def verify_incomplete_response(pwsh: str) -> None:
    with tempfile.TemporaryDirectory(prefix="vcf-logs-incomplete-") as temp_name:
        temp = Path(temp_name)
        request_log = temp / "requests.ndjson"
        ready_file = temp / "ready.json"
        server = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "mock" / "server.py"),
                "--port",
                "0",
                "--request-log",
                str(request_log),
                "--ready-file",
                str(ready_file),
                "--force-incomplete",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            ready = wait_for_ready(ready_file, server)
            base_uri = f"http://{ready['host']}:{ready['port']}"
            script = r"""
$ErrorActionPreference = 'Stop'
Import-Module -Name $env:VCF_IMPLEMENTATION -Force
$emitted = [System.Collections.Generic.List[object]]::new()
$threw = $false
try {
    Get-VcfLogEvent `
        -ServerUri ([Uri]$env:VCF_BASE_URI) `
        -SessionId 'fixture-session' `
        -StartTimestamp 1700000000000 `
        -EndTimestamp 1700000009999 `
        -PageSize 2 | ForEach-Object { $emitted.Add($_) }
} catch {
    $threw = $true
}
[pscustomobject]@{
    threw = $threw
    emittedCount = $emitted.Count
} | ConvertTo-Json -Compress
"""
            environment = os.environ.copy()
            environment["VCF_IMPLEMENTATION"] = str(IMPLEMENTATION)
            environment["VCF_BASE_URI"] = base_uri
            result = run(
                [pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                env=environment,
                timeout=20,
            )
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)

        output_lines = [line for line in result.stdout.splitlines() if line.strip()]
        if not output_lines:
            fail("incomplete-response probe produced no JSON output")
        actual = json.loads(output_lines[-1])
        if actual != {"threw": True, "emittedCount": 0}:
            fail(f"incomplete response was not rejected atomically: {actual}")


def main() -> None:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        fail("pwsh is required")
    verify_contract_pin()
    verify_module_shape(pwsh)
    verify_behavior(pwsh)
    verify_incomplete_response(pwsh)
    print("verification passed")


if __name__ == "__main__":
    main()
