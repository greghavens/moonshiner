#!/usr/bin/env python3
"""Protected deterministic verifier for the VCF Operations for Networks seed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from contract_mock import start_contract_server


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
MODULE_DIR = ROOT / "src" / "Vcf.OperationsNetworks"
SPEC_PATH = "specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"
COMMIT = "85151f6b1bb58f13b6ac0304bfec53904bea085f"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def read_json(path: Path) -> dict:
    require(path.is_file(), f"missing {path.relative_to(ROOT)}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VerificationError(f"invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc


def verify_contract() -> None:
    contract = read_json(DOCS / "contract.json")
    require(contract.get("openapi") == "3.0.1", "contract openapi must be 3.0.1")
    require(contract.get("apiVersion") == "9.0.0.0", "contract apiVersion must be 9.0.0.0")
    require(contract.get("basePath") == "/api/ni", "contract basePath must be /api/ni")
    operations = contract.get("operations")
    require(isinstance(operations, list) and len(operations) == 2, "contract must contain exactly two operations")
    by_id = {item.get("operationId"): item for item in operations}
    require(set(by_id) == {"updateVcenter", "enableVcenter"}, "contract operationIds are not exact")

    update = by_id["updateVcenter"]
    require(update.get("method") == "PUT", "updateVcenter method must be PUT")
    require(update.get("path") == "/data-sources/vcenters/{id}", "updateVcenter path is incorrect")
    require(
        update.get("pathParameters")
        == [{"name": "id", "in": "path", "required": True, "type": "string"}],
        "updateVcenter path parameter contract is incorrect",
    )
    require(
        update.get("requestBody")
        == {
            "required": False,
            "mediaType": "application/json",
            "schema": "#/components/schemas/VCenterDataSource",
            "mutableProperties": ["nickname", "notes", "credentials"],
            "requiredProperties": [],
        },
        "updateVcenter request body contract is incorrect",
    )
    require(
        update.get("responses") == [200, 400, 401, 403, 404, 500],
        "updateVcenter response statuses are incorrect",
    )

    enable = by_id["enableVcenter"]
    require(enable.get("method") == "POST", "enableVcenter method must be POST")
    require(enable.get("path") == "/data-sources/vcenters/{id}/enable", "enableVcenter path is incorrect")
    require(
        enable.get("pathParameters")
        == [{"name": "id", "in": "path", "required": True, "type": "string"}],
        "enableVcenter path parameter contract is incorrect",
    )
    require(enable.get("requestBody") is None, "enableVcenter must not have a request body")
    require(enable.get("responses") == [200, 401, 403, 404, 500], "enableVcenter response statuses are incorrect")


def verify_sources() -> None:
    sources = read_json(DOCS / "official_sources.json")
    require(sources.get("repository") == "https://github.com/vmware/vcf-api-specs", "incorrect source repository")
    require(sources.get("license") == "Apache-2.0", "incorrect source license")
    require(sources.get("tag") == "9.0.0.0", "incorrect source tag")
    require(sources.get("commit") == COMMIT, "incorrect source commit")
    require(sources.get("specPath") == SPEC_PATH, "incorrect source spec path")
    operations = sources.get("operations")
    require(
        operations
        == [
            {
                "operationId": "updateVcenter",
                "method": "PUT",
                "path": "/data-sources/vcenters/{id}",
            },
            {
                "operationId": "enableVcenter",
                "method": "POST",
                "path": "/data-sources/vcenters/{id}/enable",
            },
        ],
        "official_sources operations are incomplete or not exact",
    )


def verify_manifest() -> None:
    manifest = MODULE_DIR / "Vcf.OperationsNetworks.psd1"
    require(manifest.is_file(), "module manifest is missing")
    command = (
        "$m=Import-PowerShellDataFile -Path $env:VCF_SEED_MANIFEST;"
        "$r=@($m.RequiredModules)[0];"
        "[pscustomobject]@{ModuleName=$r.ModuleName;RequiredVersion=[string]$r.RequiredVersion;"
        "Exports=@($m.FunctionsToExport)} | ConvertTo-Json -Compress"
    )
    environment = os.environ.copy()
    environment["VCF_SEED_MANIFEST"] = str(manifest)
    result = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
    )
    require(result.returncode == 0, f"manifest could not be read: {result.stderr.strip()}")
    parsed = json.loads(result.stdout)
    require(parsed["ModuleName"] == "VMware.Sdk.Vcf.Ops", "manifest must require VMware.Sdk.Vcf.Ops")
    require(parsed["RequiredVersion"] == "13.4.0.24798382", "manifest must pin the VCF 9.0 SDK version")
    require(
        parsed["Exports"] == ["Invoke-VcfOperationsNetworksVcenterChange"],
        "manifest must export only the requested function",
    )
    vendored = [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.relative_to(ROOT).parts[0] not in {".git", ".sandbox-home"}
        and (path.suffix.lower() in {".dll", ".nupkg"} or "VMware.Sdk.Vcf" in path.as_posix())
    ]
    require(not vendored, "VMware/PowerCLI packages must not be vendored")


def verify_module_api() -> None:
    module = MODULE_DIR / "Vcf.OperationsNetworks.psm1"
    script = r"""
$ErrorActionPreference = 'Stop'
$module = Import-Module $env:VCF_SEED_MODULE -Force -PassThru
$command = Get-Command Invoke-VcfOperationsNetworksVcenterChange -ErrorAction Stop
$parameterNames = @('ServerUri', 'Token', 'Id', 'Nickname', 'Notes', 'Credential')
$parameters = [ordered]@{}
foreach ($name in $parameterNames) {
    $parameter = $command.Parameters[$name]
    $attribute = @($parameter.Attributes | Where-Object { $_ -is [System.Management.Automation.ParameterAttribute] })[0]
    $parameters[$name] = [ordered]@{
        Type = $parameter.ParameterType.FullName
        Mandatory = $attribute.Mandatory
    }
}
[pscustomobject]@{
    Exports = @($module.ExportedFunctions.Keys)
    Parameters = $parameters
} | ConvertTo-Json -Depth 8 -Compress
"""
    environment = os.environ.copy()
    environment["VCF_SEED_MODULE"] = str(module)
    completed = subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-Command", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
    )
    require(completed.returncode == 0, f"module API could not be read: {completed.stderr.strip()}")
    lines = [line for line in completed.stdout.splitlines() if line.strip().startswith("{")]
    require(lines, f"module API check returned no JSON: {completed.stdout.strip()}")
    parsed = json.loads(lines[-1])
    require(
        parsed["Exports"] == ["Invoke-VcfOperationsNetworksVcenterChange"],
        "module must export only Invoke-VcfOperationsNetworksVcenterChange",
    )
    expected = {
        "ServerUri": {"Type": "System.Uri", "Mandatory": True},
        "Token": {"Type": "System.String", "Mandatory": True},
        "Id": {"Type": "System.String", "Mandatory": True},
        "Nickname": {"Type": "System.String", "Mandatory": True},
        "Notes": {"Type": "System.String", "Mandatory": False},
        "Credential": {"Type": "System.Management.Automation.PSCredential", "Mandatory": False},
    }
    require(parsed["Parameters"] == expected, "exported function parameter contract is incorrect")


def invoke_scenario(
    base_url: str,
    *,
    entity_id: str,
    nickname: str,
    notes: str | None = None,
    credential: tuple[str, str] | None = None,
) -> dict:
    # Import the root module directly: the manifest prerequisite is verified
    # separately and need not be installed to exercise this REST-only function.
    module = MODULE_DIR / "Vcf.OperationsNetworks.psm1"
    script = r"""
$ErrorActionPreference = 'Stop'
Import-Module $args[0] -Force
$parameters = @{
    ServerUri = $args[1]
    Token = 'fixture-token'
    Id = $args[2]
    Nickname = $args[3]
}
if ($args[4] -ne '__UNSET__') {
    $parameters.Notes = $args[4]
}
if ($args[5] -ne '__UNSET__') {
    $securePassword = ConvertTo-SecureString $args[6] -AsPlainText -Force
    $parameters.Credential = [pscredential]::new($args[5], $securePassword)
}
$result = Invoke-VcfOperationsNetworksVcenterChange @parameters
$result | ConvertTo-Json -Depth 12 -Compress
"""
    note_argument = "__UNSET__" if notes is None else notes
    credential_arguments = ("__UNSET__", "__UNSET__") if credential is None else credential
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", encoding="utf-8", delete=False) as handle:
        handle.write(script)
        script_path = Path(handle.name)
    try:
        completed = subprocess.run(
            [
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-File",
                str(script_path),
                str(module),
                base_url,
                entity_id,
                nickname,
                note_argument,
                *credential_arguments,
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=40,
            env=os.environ.copy(),
        )
    finally:
        script_path.unlink(missing_ok=True)
    require(completed.returncode == 0, f"PowerShell scenario failed: {completed.stderr.strip()}")
    lines = [line for line in completed.stdout.splitlines() if line.strip().startswith("{")]
    require(lines, f"PowerShell scenario returned no JSON summary: {completed.stdout.strip()}")
    return json.loads(lines[-1])


def run_scenario(
    *,
    entity_id: str,
    nickname: str,
    notes: str | None = None,
    credential: tuple[str, str] | None = None,
    responses: dict[str, tuple[int, dict]] | None = None,
) -> tuple[dict, list[dict]]:
    with tempfile.TemporaryDirectory() as temp_dir:
        log_path = Path(temp_dir) / "requests.jsonl"
        server, thread = start_contract_server(DOCS / "contract.json", log_path, responses)
        try:
            summary = invoke_scenario(
                server.base_url,
                entity_id=entity_id,
                nickname=nickname,
                notes=notes,
                credential=credential,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    return summary, entries


def verify_failure_scenario() -> None:
    summary, entries = run_scenario(entity_id="18230:902:993642895", nickname="VC-East")

    require(summary.get("Succeeded") is False, "summary must report the failed two-step change")
    steps = summary.get("Steps")
    require(isinstance(steps, list) and len(steps) == 2, "summary must contain two ordered steps")
    require(
        steps[0]
        == {
            "OperationId": "updateVcenter",
            "Outcome": "Succeeded",
            "HttpStatus": 200,
            "ErrorCode": None,
            "Message": None,
        },
        "successful update step was not reported accurately",
    )
    require(
        steps[1]
        == {
            "OperationId": "enableVcenter",
            "Outcome": "Failed",
            "HttpStatus": 500,
            "ErrorCode": "COLLECTOR_UNAVAILABLE",
            "Message": "The collector is offline",
        },
        "failed enable step was not reported accurately",
    )

    require(len(entries) == 2, "the workflow must send exactly the two contract operations")
    update, enable = entries
    require(update["operationId"] == "updateVcenter" and update["method"] == "PUT", "first request is not updateVcenter")
    require(
        update["requestTarget"] == "/api/ni/data-sources/vcenters/18230%3A902%3A993642895",
        "updateVcenter request target is incorrect",
    )
    require(update["path"] == "/api/ni/data-sources/vcenters/18230:902:993642895", "updateVcenter request path is incorrect")
    require(update["headers"].get("authorization") == "NetworkInsight fixture-token", "authorization header is incorrect")
    require(update["headers"].get("accept") == "application/json", "update Accept header is incorrect")
    require(update["headers"].get("content-type", "").split(";", 1)[0] == "application/json", "update Content-Type is incorrect")
    require(update["headers"].get("content-length") == "22", "update Content-Length is incorrect")
    update_body = json.loads(update["body"])
    require(update_body == {"nickname": "VC-East"}, "unset optional update properties must be omitted")

    require(enable["operationId"] == "enableVcenter" and enable["method"] == "POST", "second request is not enableVcenter")
    require(
        enable["requestTarget"] == "/api/ni/data-sources/vcenters/18230%3A902%3A993642895/enable",
        "enableVcenter request target is incorrect",
    )
    require(enable["path"] == "/api/ni/data-sources/vcenters/18230:902:993642895/enable", "enableVcenter request path is incorrect")
    require(enable["headers"].get("authorization") == "NetworkInsight fixture-token", "enable authorization header is incorrect")
    require(enable["headers"].get("accept") == "application/json", "enable Accept header is incorrect")
    require(enable["headers"].get("content-length") == "0", "enable Content-Length is incorrect")
    require("content-type" not in enable["headers"], "enableVcenter must not send Content-Type without a body")
    require(enable["body"] == "", "enableVcenter must not send a request body")


def verify_supplied_values_scenario() -> None:
    entity_id = "vc west/one?#"
    summary, entries = run_scenario(
        entity_id=entity_id,
        nickname="VC-West",
        notes="",
        credential=("svc-vcenter", "p@ss word!"),
        responses={"enableVcenter": (200, {})},
    )

    require(summary.get("Succeeded") is True, "summary must report a successful two-step change")
    require(
        summary.get("Steps")
        == [
            {
                "OperationId": "updateVcenter",
                "Outcome": "Succeeded",
                "HttpStatus": 200,
                "ErrorCode": None,
                "Message": None,
            },
            {
                "OperationId": "enableVcenter",
                "Outcome": "Succeeded",
                "HttpStatus": 200,
                "ErrorCode": None,
                "Message": None,
            },
        ],
        "successful ordered step summary is incorrect",
    )
    require(len(entries) == 2, "successful workflow must send exactly two requests")
    update, enable = entries
    encoded_id = "vc%20west%2Fone%3F%23"
    require(
        update["requestTarget"] == f"/api/ni/data-sources/vcenters/{encoded_id}",
        "Id must be escaped as exactly one update path segment",
    )
    require(
        enable["requestTarget"] == f"/api/ni/data-sources/vcenters/{encoded_id}/enable",
        "Id must be escaped as exactly one enable path segment",
    )
    require(
        json.loads(update["body"])
        == {
            "nickname": "VC-West",
            "notes": "",
            "credentials": {"username": "svc-vcenter", "password": "p@ss word!"},
        },
        "supplied notes and credentials must be represented exactly in the update body",
    )
    require(enable["body"] == "", "successful enableVcenter must remain bodyless")
    require("content-type" not in enable["headers"], "successful enableVcenter must omit Content-Type")


def main() -> int:
    try:
        verify_contract()
        verify_sources()
        verify_manifest()
        verify_module_api()
        verify_failure_scenario()
        verify_supplied_values_scenario()
    except (VerificationError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: contract, provenance, SDK prerequisite, wire shape, and failure reporting verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
