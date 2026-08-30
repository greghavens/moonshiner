#!/usr/bin/env python3
"""Deterministic protected verifier for vcf91-0222.

Proves that Invoke-VcfSddcLcmDepotSync survives a mid-run access token expiry:
the expired lifecycle request is replayed byte for byte after exactly one genuine
SDK refresh, no already-accepted work is reissued, and every unset optional member
stays off the wire instead of being sent empty. No live VMware endpoint is used.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contract.json"
SOURCES_PATH = ROOT / "docs" / "official_sources.json"
MANIFEST_PATH = ROOT / "src" / "VcfSddcLcm.DepotSync" / "VcfSddcLcm.DepotSync.psd1"
MODULE_PATH = ROOT / "src" / "VcfSddcLcm.DepotSync" / "VcfSddcLcm.DepotSync.psm1"
MOCK_PATH = ROOT / ".protected" / "mock_server.py"
INVOKER_PATH = ROOT / ".protected" / "invoke_case.ps1"

COMMIT = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26"
LCM_SPEC_PATH = "specifications/sddc-lcm/sddc-lcm-openapi.yaml"
MANAGER_SPEC_PATH = "specifications/sddc-manager/sddc-manager-openapi.json"
OPERATION_IDS = [
    "createToken",
    "refreshAccessToken",
    "setDepot",
    "getTask",
    "resolveDepotComponents",
]
SESSION_OPERATION_IDS = ["createToken", "refreshAccessToken"]
LIFECYCLE_OPERATION_IDS = ["setDepot", "getTask", "resolveDepotComponents"]

SDK_MODULE_NAME = "VMware.Sdk.Vcf.SddcManager"
SDK_MODULE_VERSION = "13.5.0.25380678"
SDK_USER_AGENT_PREFIX = "VMware.Sdk.Vcf.SddcManager/"
CONNECT_USER_AGENT = "PowerCLI"

# The mock stops accepting the original access token after this many authenticated
# SDDC LCM requests, so setDepot and getTask succeed and resolveDepotComponents expires.
LIFECYCLE_CALLS_BEFORE_EXPIRY = 2

EXPECTED_ROUTES = {
    "createToken": ("sddc-manager", "POST", "/v1/tokens"),
    "refreshAccessToken": ("sddc-manager", "PATCH", "/v1/tokens/access-token/refresh"),
    "setDepot": ("sddc-lcm", "POST", "/v1/depot"),
    "getTask": ("sddc-lcm", "GET", "/v1/tasks/{taskId}"),
    "resolveDepotComponents": ("sddc-lcm", "POST", "/v1/depot/components"),
}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> Any:
    require(path.is_file(), f"missing protected fixture {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- contract


def verify_contract() -> None:
    contract = load_json(CONTRACT_PATH)
    sources = load_json(SOURCES_PATH)

    source = contract.get("source", {})
    auth_source = contract.get("authSource", {})
    require(
        source.get("repositoryCommitSha") == COMMIT
        and auth_source.get("repositoryCommitSha") == COMMIT,
        "contract must pin repository commit " + COMMIT,
    )
    require(
        source.get("specPath") == LCM_SPEC_PATH,
        f"contract primary specification must be {LCM_SPEC_PATH}",
    )
    require(
        auth_source.get("specPath") == MANAGER_SPEC_PATH,
        f"contract auth specification must be {MANAGER_SPEC_PATH}",
    )
    require(
        source.get("repository") == "vmware/vcf-api-specs"
        and source.get("license") == "Apache-2.0",
        "contract must record the Apache-2.0 vmware/vcf-api-specs repository",
    )

    operations = contract.get("operations", [])
    require(
        [item.get("operationId") for item in operations] == OPERATION_IDS,
        "contract must name exactly these operationIds in order: "
        + ", ".join(OPERATION_IDS),
    )
    for item in operations:
        operation_id = item["operationId"]
        expected = EXPECTED_ROUTES[operation_id]
        actual = (item.get("service"), item.get("method"), item.get("path"))
        require(
            actual == expected,
            f"contract route for {operation_id} must be {expected}, found {actual}",
        )

    # Only the two SDDC Manager session operations have a generated PowerCLI binding;
    # VMware publishes no VMware.Sdk.Vcf module for the SDDC LCM service.
    bindings = {item["operationId"]: item.get("bindingCommand") for item in operations}
    require(
        bindings["createToken"] == "Invoke-VcfCreateToken"
        and bindings["refreshAccessToken"] == "Invoke-VcfRefreshAccessToken",
        "contract must bind the session operations to the genuine SDK commands",
    )
    for operation_id in LIFECYCLE_OPERATION_IDS:
        require(
            bindings[operation_id] is None,
            f"{operation_id} has no PowerCLI binding and must record bindingCommand null",
        )

    schemas = contract.get("schemas", {})
    require(
        schemas.get("FleetDepotSpec", {}).get("required") == ["certificate", "fqdn"],
        "FleetDepotSpec required members must be certificate and fqdn",
    )
    require(
        schemas.get("DepotComponentsSpec", {}).get("required")
        == ["componentVersions", "fleetDepotSpec"],
        "DepotComponentsSpec required members must be componentVersions and fleetDepotSpec",
    )
    require(
        schemas.get("ComponentVersionSpec", {}).get("required") == ["component"],
        "ComponentVersionSpec must require only component",
    )
    require(
        "version" in schemas.get("DepotComponentsSpec", {}).get("properties", {})
        and "version" in schemas.get("ComponentVersionSpec", {}).get("properties", {}),
        "the optional version members must be projected into the contract",
    )

    profile = contract.get("exerciseWireProfile", {})
    require(profile.get("unsetBehavior") == "omit", "unset optional members must be omitted")
    require(
        profile.get("lifecycleCallOrder") == LIFECYCLE_OPERATION_IDS,
        "contract must pin the lifecycle call order",
    )
    require(
        profile.get("refreshRequestBodyIsBareJsonString") is True,
        "contract must record that the refresh request body is a bare JSON string",
    )

    # provenance
    require(
        sources.get("repositoryCommitSha") == COMMIT,
        "official_sources must pin repository commit " + COMMIT,
    )
    require(
        sources.get("license") == "Apache-2.0"
        and sources.get("repository") == "vmware/vcf-api-specs",
        "official_sources must record the Apache-2.0 vmware/vcf-api-specs repository",
    )
    require(
        sources.get("operationIds") == OPERATION_IDS,
        "official_sources must record every operationId used",
    )
    spec_paths = {entry.get("specPath") for entry in sources.get("specs", [])}
    require(
        spec_paths == {LCM_SPEC_PATH, MANAGER_SPEC_PATH},
        "official_sources must record both pinned specification paths",
    )
    recorded = {entry.get("operationId"): entry for entry in sources.get("operations", [])}
    require(
        set(recorded) == set(OPERATION_IDS),
        "official_sources must record provenance for every operationId",
    )
    for operation_id, entry in recorded.items():
        service, method, path = EXPECTED_ROUTES[operation_id]
        require(
            entry.get("method") == method and entry.get("path") == path,
            f"official_sources route for {operation_id} disagrees with the contract",
        )
        require(
            entry.get("repositoryCommitSha") == COMMIT,
            f"official_sources must pin {operation_id} to commit {COMMIT}",
        )
        expected_spec = LCM_SPEC_PATH if service == "sddc-lcm" else MANAGER_SPEC_PATH
        require(
            entry.get("specPath") == expected_spec,
            f"official_sources must source {operation_id} from {expected_spec}",
        )
    require(
        sources.get("derivation", {}).get("documentationPageUsedAsContractSource")
        is False,
        "the contract must be derived from the specification, not a documentation page",
    )


# ------------------------------------------------------------- powershell


def run_pwsh(command: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        env={
            **os.environ,
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "POWERSHELL_UPDATECHECK": "Off",
        },
    )


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def verify_manifest_and_sdk() -> None:
    command = (
        f"$d=Import-PowerShellDataFile -LiteralPath {ps_quote(str(MANIFEST_PATH))};"
        "if($d.RootModule -cne 'VcfSddcLcm.DepotSync.psm1'){exit 2};"
        "if($d.PowerShellVersion -cne '7.4'){exit 3};"
        "if(($d.FunctionsToExport -join ',') -cne 'Invoke-VcfSddcLcmDepotSync'){exit 4};"
        "if($d.RequiredModules.Count -ne 1){exit 5};"
        "$r=$d.RequiredModules[0];"
        f"if($r.ModuleName -cne '{SDK_MODULE_NAME}' -or "
        f"[string]$r.RequiredVersion -cne '{SDK_MODULE_VERSION}'){{exit 6}};"
        f"Import-Module '{SDK_MODULE_NAME}' -RequiredVersion '{SDK_MODULE_VERSION}' "
        "-Force -ErrorAction Stop;"
        # The genuine generated bindings must agree with the pinned specification.
        "$expected=@{"
        "createToken=@('POST','/v1/tokens','Invoke-VcfCreateToken');"
        "refreshAccessToken=@('PATCH','/v1/tokens/access-token/refresh',"
        "'Invoke-VcfRefreshAccessToken')};"
        "foreach($id in @('createToken','refreshAccessToken')){"
        "$o=@(Get-VcfSddcManagerOperation -Name $id);"
        "if($o.Count -ne 1 -or $null -eq $o[0].CommandInfo){exit 7};"
        "if($o[0].Method.ToString() -cne $expected[$id][0] "
        "-or $o[0].Path -cne $expected[$id][1]){exit 8};"
        "$c=Get-Command $o[0].CommandInfo.Name -ErrorAction Stop;"
        f"if($c.Source -cne '{SDK_MODULE_NAME}' -or "
        "$c.Name -cne $expected[$id][2]){exit 9}};"
        # The SDDC LCM service ships no PowerCLI module: the identical operationIds that
        # do exist in the SDDC Manager SDK denote a different wire contract and must not
        # be mistaken for the SDDC LCM ones.
        "$t=@(Get-VcfSddcManagerOperation -Name 'getTask');"
        "if($t.Count -ne 1 -or $t[0].Path -cne '/v1/tasks/{id}'){exit 10};"
        "foreach($id in @('setDepot','resolveDepotComponents')){"
        "if(@(Get-VcfSddcManagerOperation -Name $id).Count -ne 0){exit 11}}"
    )
    result = run_pwsh(command)
    require(
        result.returncode == 0,
        "protected manifest or genuine SDDC Manager SDK prerequisite is invalid "
        f"(exit {result.returncode}): "
        + (result.stderr.strip() or result.stdout.strip()),
    )


def verify_solution_shape() -> None:
    require(MODULE_PATH.is_file(), "create VcfSddcLcm.DepotSync.psm1")
    # Parse commands and function definitions instead of searching raw source text:
    # comments should neither satisfy a required SDK call nor trip a forbidden one.
    ast_command = (
        "$tokens=$null;$errors=$null;"
        "$ast=[Management.Automation.Language.Parser]::ParseFile("
        f"{ps_quote(str(MODULE_PATH))},[ref]$tokens,[ref]$errors);"
        "if($errors.Count -ne 0){$errors | ForEach-Object {$_.ToString()} | "
        "Write-Error;exit 20};"
        "$commands=@($ast.FindAll({param($n)"
        "$n -is [Management.Automation.Language.CommandAst]},$true) | "
        "ForEach-Object {$_.GetCommandName()} | Where-Object {$null -ne $_});"
        "$functions=@($ast.FindAll({param($n)"
        "$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true) | "
        "ForEach-Object {$_.Name});"
        "$throws=@($ast.FindAll({param($n)"
        "$n -is [Management.Automation.Language.ThrowStatementAst]},$true) | "
        "ForEach-Object {$_.Extent.Text});"
        "[ordered]@{commands=$commands;functions=$functions;throws=$throws} | "
        "ConvertTo-Json -Depth 4 -Compress"
    )
    ast_result = run_pwsh(ast_command)
    require(
        ast_result.returncode == 0,
        "implementation must be valid PowerShell syntax: "
        + (ast_result.stderr.strip() or ast_result.stdout.strip()),
    )
    ast_shape = json.loads(ast_result.stdout)

    def base_command(name: str) -> str:
        return name.rsplit("\\", 1)[-1].casefold()

    commands = [base_command(name) for name in ast_shape.get("commands", [])]
    functions = {name.casefold() for name in ast_shape.get("functions", [])}
    throws = [text.casefold() for text in ast_shape.get("throws", [])]
    require(
        all("has not been implemented" not in text for text in throws),
        "Invoke-VcfSddcLcmDepotSync is still the unimplemented stub",
    )
    for command in (
        "Initialize-VcfTokenCreationSpec",
        "Invoke-VcfCreateToken",
        "Invoke-VcfRefreshAccessToken",
    ):
        require(
            command.casefold() in commands,
            f"the implementation must directly invoke the genuine SDK binding {command}",
        )
    for forbidden in (
        "Connect-VcfSddcManagerServer",
        "Disconnect-VcfSddcManagerServer",
        "Install-Module",
        "Save-Module",
    ):
        require(
            forbidden.casefold() not in commands,
            f"the implementation must not invoke {forbidden}",
        )
    for protected_name in (
        "Initialize-VcfTokenCreationSpec",
        "Invoke-VcfCreateToken",
        "Invoke-VcfRefreshAccessToken",
    ):
        require(
            protected_name.casefold() not in functions,
            f"do not redefine the VMware SDK command {protected_name}",
        )


# ------------------------------------------------------------- runtime


def wait_for_ports(port_file: Path, process: subprocess.Popen[str]) -> tuple[int, int]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=5)
            raise VerificationError(
                "loopback mock exited during startup: " + (stderr or stdout).strip()
            )
        if port_file.is_file():
            value = port_file.read_text(encoding="utf-8").strip()
            if value:
                try:
                    published = json.loads(value)
                except json.JSONDecodeError:
                    published = None
                if isinstance(published, dict):
                    return (
                        int(published["sddcManagerPort"]),
                        int(published["sddcLcmPort"]),
                    )
        time.sleep(0.04)
    raise VerificationError("loopback mock did not publish its ports")


def read_log(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def one_header(request: dict[str, Any], name: str) -> str:
    values = request.get("headerValues", {}).get(name.casefold(), [])
    require(
        len(values) == 1,
        f"{name} must occur exactly once on {request['method']} {request['path']}",
    )
    return values[0]


def header_absent(request: dict[str, Any], name: str) -> None:
    values = request.get("headerValues", {}).get(name.casefold(), [])
    require(
        not values,
        f"{name} is not defined for {request['method']} {request['path']} "
        "and must be absent, not sent empty",
    )


def assert_no_empty_members(value: Any, trail: str) -> None:
    """An omitted optional member must be absent, never null or an empty string."""
    if isinstance(value, dict):
        for key, item in value.items():
            require(
                item is not None,
                f"{trail}.{key} was sent as null; an unset optional member must be omitted",
            )
            require(
                item != "",
                f"{trail}.{key} was sent empty; an unset optional member must be omitted",
            )
            assert_no_empty_members(item, f"{trail}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            require(
                item is not None,
                f"{trail}[{index}] was sent as null",
            )
            assert_no_empty_members(item, f"{trail}[{index}]")


def make_scenario() -> dict[str, Any]:
    suffix = secrets.token_hex(6)
    return {
        "username": f"svc-{suffix}@vsphere.local",
        "password": "Pw-" + secrets.token_hex(8),
        "accessToken": "acc-original-" + secrets.token_hex(10),
        "refreshTokenId": "ref-" + secrets.token_hex(10),
        "refreshedAccessToken": "acc-refreshed-" + secrets.token_hex(10),
        "taskId": str(uuid.uuid4()),
        "depotFqdn": f"depot-{suffix}.lab.example",
        "depotCertificate": "-----BEGIN CERTIFICATE-----"
        + secrets.token_hex(16)
        + "-----END CERTIFICATE-----",
        "lifecycleCallsBeforeExpiry": LIFECYCLE_CALLS_BEFORE_EXPIRY,
        "pinnedComponent": "VCENTER",
        "pinnedComponentVersion": "9.1.0.0",
        "unpinnedComponent": "NSX",
        "correlationId": "corr-" + secrets.token_hex(8),
        "resolvedComponentVersions": [
            {
                "component": "VCENTER",
                "version": "9.1.0.0",
                "binaryUrl": f"https://depot-{suffix}.lab.example/bin/vc.iso",
            },
            {
                "component": "NSX",
                "version": "9.1.0.1",
                "binaryUrl": f"https://depot-{suffix}.lab.example/bin/nsx.zip",
            },
        ],
    }


def split_phases(log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Separate the caller's connection handshake from the module's own requests.

    Connect-VcfSddcManagerServer identifies itself as 'PowerCLI'; every request the
    module drives through a generated binding carries the versioned SDK user agent.
    """
    first_module = None
    for index, request in enumerate(log):
        agent = (request.get("headerValues", {}).get("user-agent") or [""])[0]
        if agent.startswith(SDK_USER_AGENT_PREFIX):
            first_module = index
            break
    require(
        first_module is not None,
        "no request carried the genuine VMware.Sdk.Vcf.SddcManager user agent; the "
        "session operations must go through the generated SDK bindings",
    )

    handshake = log[:first_module]
    require(
        len(handshake) == 2,
        f"expected exactly 2 caller connection requests, found {len(handshake)}",
    )
    require(
        handshake[0]["operationId"] == "createToken"
        and handshake[1]["handshake"] is True,
        "the caller connection must be a createToken followed by the SDK handshake",
    )
    for request in handshake:
        require(
            one_header(request, "User-Agent") == CONNECT_USER_AGENT,
            "the caller connection requests must come from Connect-VcfSddcManagerServer",
        )
    return log[first_module:]


def verify_runtime() -> None:
    require(shutil.which("pwsh") is not None, "pwsh is required")
    require(MOCK_PATH.is_file() and INVOKER_PATH.is_file(), "protected harness is missing")
    scenario = make_scenario()

    with tempfile.TemporaryDirectory(prefix="vcf91-0222-") as temporary:
        temp = Path(temporary)
        port_file = temp / "ports.json"
        request_log = temp / "requests.jsonl"
        scenario_file = temp / "scenario.json"
        result_file = temp / "result.json"
        scenario_file.write_text(
            json.dumps(scenario, separators=(",", ":")), encoding="utf-8"
        )
        environment = {
            **os.environ,
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "POWERSHELL_TELEMETRY_OPTOUT": "1",
            "POWERSHELL_UPDATECHECK": "Off",
        }
        server = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(MOCK_PATH),
                str(port_file),
                str(request_log),
                str(CONTRACT_PATH),
                str(scenario_file),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        try:
            manager_port, lcm_port = wait_for_ports(port_file, server)
            invocation = subprocess.run(
                [
                    "pwsh",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    str(INVOKER_PATH),
                    "-ModuleManifest", str(MANIFEST_PATH),
                    "-SddcManagerHost", "127.0.0.1",
                    "-SddcManagerPort", str(manager_port),
                    "-LcmBaseUrl", f"http://127.0.0.1:{lcm_port}",
                    "-Username", scenario["username"],
                    "-Password", scenario["password"],
                    "-DepotFqdn", scenario["depotFqdn"],
                    "-DepotCertificate", scenario["depotCertificate"],
                    "-PinnedComponent", scenario["pinnedComponent"],
                    "-PinnedComponentVersion", scenario["pinnedComponentVersion"],
                    "-UnpinnedComponent", scenario["unpinnedComponent"],
                    "-CorrelationId", scenario["correlationId"],
                    "-OutputPath", str(result_file),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=300,
                check=False,
                env=environment,
            )
        finally:
            server.terminate()
            try:
                server.communicate(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                server.kill()
                server.communicate(timeout=10)

        require(
            invocation.returncode == 0,
            "the depot sync run failed: "
            + (invocation.stderr.strip() or invocation.stdout.strip()),
        )
        require(result_file.is_file(), "the depot sync run produced no result")
        result = json.loads(result_file.read_text(encoding="utf-8"))
        log = read_log(request_log)

    verify_request_log(log, scenario)
    verify_result(result, scenario)


def verify_request_log(log: list[dict[str, Any]], scenario: dict[str, Any]) -> None:
    module_requests = split_phases(log)

    require(
        all(request["responseStatus"] != 404 for request in module_requests),
        "a request reached a route the contract does not name",
    )
    require(
        not any(request["handshake"] for request in module_requests),
        "the module must not repeat the caller's connection handshake",
    )

    observed = [
        (request["operationId"], request["responseStatus"])
        for request in module_requests
    ]
    expected = [
        ("createToken", 201),
        ("setDepot", 202),
        ("getTask", 200),
        ("resolveDepotComponents", 401),
        ("refreshAccessToken", 200),
        ("resolveDepotComponents", 200),
    ]
    require(
        observed == expected,
        "the module's request sequence must be "
        + " -> ".join(f"{name}({status})" for name, status in expected)
        + ", found "
        + " -> ".join(f"{name}({status})" for name, status in observed),
    )

    create, set_depot, get_task, expired, refresh, retried = module_requests

    original = scenario["accessToken"]
    refreshed = scenario["refreshedAccessToken"]

    # -- session bootstrap: createToken through the genuine SDK binding ----
    require(
        one_header(create, "User-Agent").startswith(SDK_USER_AGENT_PREFIX),
        "createToken must be issued by the generated SDK binding",
    )
    require(create["rawQuery"] == "", "createToken must not carry a query string")
    create_body = json.loads(create["body"])
    require(
        sorted(create_body) == ["password", "username"],
        "TokenCreationSpec must carry exactly username and password on the wire; the "
        "unset optional apiKey and idToken members must be omitted, found "
        + ", ".join(sorted(create_body)),
    )
    require(
        create_body["username"] == scenario["username"]
        and create_body["password"] == scenario["password"],
        "createToken must send the caller's credential unchanged",
    )
    assert_no_empty_members(create_body, "TokenCreationSpec")

    # -- setDepot -----------------------------------------------------------
    require(
        set_depot["rawTarget"] == "/v1/depot",
        f"setDepot target must be /v1/depot, found {set_depot['rawTarget']}",
    )
    require(
        one_header(set_depot, "Authorization") == f"Bearer {original}",
        "setDepot must present the originally minted access token",
    )
    require(
        one_header(set_depot, "Content-Type").split(";")[0].strip().casefold()
        == "application/json",
        "setDepot must send JSON",
    )
    require(
        one_header(set_depot, "X-Correlation-Id") == scenario["correlationId"],
        "setDepot must propagate the caller's X-Correlation-Id verbatim",
    )
    depot_body = json.loads(set_depot["body"])
    require(
        sorted(depot_body) == ["certificate", "fqdn"],
        "FleetDepotSpec must carry exactly fqdn and certificate, found "
        + ", ".join(sorted(depot_body)),
    )
    require(
        depot_body["fqdn"] == scenario["depotFqdn"]
        and depot_body["certificate"] == scenario["depotCertificate"],
        "setDepot must send the caller's fleet depot endpoint unchanged",
    )
    assert_no_empty_members(depot_body, "FleetDepotSpec")

    # -- getTask ------------------------------------------------------------
    task_id = scenario["taskId"]
    require(
        get_task["rawTarget"] == f"/v1/tasks/{task_id}",
        f"getTask target must be /v1/tasks/{task_id}, found {get_task['rawTarget']}",
    )
    require(
        get_task["bodyLength"] == 0,
        "getTask defines no request body and must not send one",
    )
    require(
        one_header(get_task, "Authorization") == f"Bearer {original}",
        "getTask must still present the originally minted access token",
    )
    # X-Correlation-Id is defined only on setDepot.
    header_absent(get_task, "X-Correlation-Id")

    # -- the request that met the expired token ------------------------------
    require(
        expired["rawTarget"] == "/v1/depot/components",
        "resolveDepotComponents target must be /v1/depot/components",
    )
    require(
        one_header(expired, "Authorization") == f"Bearer {original}",
        "the expired attempt must be the one carrying the original access token",
    )
    header_absent(expired, "X-Correlation-Id")
    resolve_body = json.loads(expired["body"])
    require(
        sorted(resolve_body) == ["componentVersions", "fleetDepotSpec"],
        "DepotComponentsSpec must carry exactly fleetDepotSpec and componentVersions; "
        "the unset optional top-level version member must be omitted, found "
        + ", ".join(sorted(resolve_body)),
    )
    require(
        resolve_body["fleetDepotSpec"] == depot_body,
        "resolveDepotComponents must reuse the same fleet depot endpoint",
    )
    components = resolve_body["componentVersions"]
    require(
        isinstance(components, list) and len(components) == 2,
        "componentVersions must be a two element JSON array",
    )
    require(
        sorted(components[0]) == ["component", "version"]
        and components[0]["component"] == scenario["pinnedComponent"]
        and components[0]["version"] == scenario["pinnedComponentVersion"],
        "the pinned component must send both component and version",
    )
    require(
        list(components[1]) == ["component"]
        and components[1]["component"] == scenario["unpinnedComponent"],
        "the unpinned component's optional version member must be omitted entirely, "
        "not sent as null or an empty string, found "
        + json.dumps(components[1], sort_keys=True),
    )
    assert_no_empty_members(resolve_body, "DepotComponentsSpec")

    # -- exactly one genuine SDK refresh, between the two attempts -----------
    require(
        refresh["method"] == "PATCH",
        "refreshAccessToken is a PATCH in the pinned specification, found "
        + refresh["method"],
    )
    require(
        refresh["rawTarget"] == "/v1/tokens/access-token/refresh",
        "refreshAccessToken target must be /v1/tokens/access-token/refresh",
    )
    require(
        one_header(refresh, "User-Agent").startswith(SDK_USER_AGENT_PREFIX),
        "refreshAccessToken must be issued by the generated SDK binding",
    )
    require(
        json.loads(refresh["body"]) == scenario["refreshTokenId"],
        "the refresh request body must be the refresh token id as a bare JSON string",
    )
    require(
        refresh["body"] == json.dumps(scenario["refreshTokenId"]),
        "the refresh request body must be exactly the bare JSON string "
        + json.dumps(scenario["refreshTokenId"])
        + ", found "
        + refresh["body"],
    )

    # -- the replay: same request, new token, nothing else reissued ----------
    require(
        retried["rawTarget"] == expired["rawTarget"],
        "the replay must target the same resource as the unauthorized request",
    )
    require(
        retried["method"] == expired["method"],
        "the replay must use the same HTTP method as the unauthorized request",
    )
    require(
        retried["body"] == expired["body"],
        "the replay must repeat the unauthorized request body byte for byte, found "
        + retried["body"],
    )
    require(
        one_header(retried, "Authorization") == f"Bearer {refreshed}",
        "the replay must present the refreshed access token",
    )
    original_headers = {
        name: values
        for name, values in expired["headerValues"].items()
        if name != "authorization"
    }
    replay_headers = {
        name: values
        for name, values in retried["headerValues"].items()
        if name != "authorization"
    }
    require(
        replay_headers == original_headers,
        "the replay must preserve every request header except Authorization",
    )

    counts = {
        operation_id: sum(
            1 for request in module_requests if request["operationId"] == operation_id
        )
        for operation_id in OPERATION_IDS
    }
    require(
        counts["setDepot"] == 1,
        f"setDepot was already accepted and must not be reissued, saw {counts['setDepot']}",
    )
    require(
        counts["getTask"] == 1,
        f"getTask already succeeded and must not be reissued, saw {counts['getTask']}",
    )
    require(
        counts["createToken"] == 1,
        "the expiry must be recovered by refreshing, not by minting a second token pair",
    )
    require(
        counts["refreshAccessToken"] == 1,
        f"the access token must be refreshed exactly once, saw {counts['refreshAccessToken']}",
    )
    require(
        counts["resolveDepotComponents"] == 2,
        "only the unauthorized request may be replayed",
    )

    for request in module_requests:
        require(
            request["rawQuery"] == "",
            f"{request['operationId']} defines no query parameter; "
            f"found query {request['rawQuery']!r}",
        )
        if request["operationId"] in LIFECYCLE_OPERATION_IDS:
            token = one_header(request, "Authorization")
            require(
                token in (f"Bearer {original}", f"Bearer {refreshed}"),
                "every SDDC LCM request must present a bearer token from the session",
            )
        if request["operationId"] != "setDepot":
            header_absent(request, "X-Correlation-Id")


def verify_result(result: dict[str, Any], scenario: dict[str, Any]) -> None:
    require(
        result.get("taskId") == scenario["taskId"],
        "the returned task id must be the one setDepot issued before the expiry",
    )
    require(
        result.get("taskStatus") == "SUCCEEDED",
        "the returned task status must be the SUCCEEDED observed before the expiry",
    )
    require(
        result.get("refreshCount") == 1,
        f"the run must report exactly one refresh, found {result.get('refreshCount')}",
    )
    require(
        result.get("serverStillConnected") is True,
        "the caller's connection must be left connected",
    )
    require(
        result.get("serverUser") == scenario["username"],
        "the caller's connection must be left untouched",
    )
    require(
        result.get("resolvedComponents") == scenario["resolvedComponentVersions"],
        "the resolved component versions must be returned exactly as the service "
        "reported them after the refresh",
    )


def main() -> int:
    verify_contract()
    verify_manifest_and_sdk()
    verify_solution_shape()
    verify_runtime()
    print(
        "PASS: the expired SDDC LCM request was replayed byte for byte after exactly "
        "one genuine SDK token refresh, no accepted work was reissued, and every "
        "unset optional member stayed off the wire"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, KeyError, OSError, TypeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
