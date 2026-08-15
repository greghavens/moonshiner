#!/usr/bin/env python3
"""Deterministic offline verifier for the VCF brownfield architecture seed."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "out" / "migration-plan.json"
INVENTORY_PATH = ROOT / "fixtures" / "estate-inventory.json"
COMPATIBILITY_PATH = ROOT / "compatibility" / "pinned-compatibility.json"
PLAN_SCHEMA_PATH = ROOT / "schemas" / "migration-plan.schema.json"
OPENAPI_PATH = (
    ROOT / "specifications" / "vcf-installer" / "vcf-installer-openapi.json"
)
MODULE_DIR = ROOT / "VcfBrownfieldArchitecture"
MANIFEST_PATH = MODULE_DIR / "VcfBrownfieldArchitecture.psd1"
IMPLEMENTATION_PATH = MODULE_DIR / "VcfBrownfieldArchitecture.psm1"
RESEARCH_PATH = ROOT / "research" / "consulted-sources.md"

PROTECTED_HASHES = {
    "fixtures/estate-inventory.json": "b319e3e1d39f33d7db13e0323ca928fa151a44c7d65661c8218050aab2d1c3f7",
    "compatibility/pinned-compatibility.json": "fe0b43edf2574cfe8d246bf3f387c9ed7d4d1e4f0d8f0dd384fb81e2f4a5809e",
    "schemas/migration-plan.schema.json": "46ceb8f2bcf1cc94aff0be9ee1a320fb2cc2c1de27f489a1f541aac50139600d",
    "specifications/vcf-installer/vcf-installer-openapi.json": "29be24ab4d779edc58167e4d572782ae6718317fa8e659b154aec28cf9de263d",
    "specifications/vcf-installer/SOURCE.json": "a807b3d52cc71a5ae806444b357fbfee369d0372a700ed8285a7e4819492020a",
    "specifications/vcf-installer/LICENSE": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
}


class VerificationError(AssertionError):
    pass


class SchemaError(VerificationError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError(f"missing required file: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationError(
            f"invalid JSON in {path.relative_to(ROOT)}: {exc}"
        ) from exc


def json_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise SchemaError(f"unsupported schema type {expected!r}")


def resolve_ref(root_schema: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaError(f"external schema reference is not supported: {ref}")
    current: Any = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        try:
            current = current[part]
        except (KeyError, TypeError) as exc:
            raise SchemaError(f"unresolvable schema reference: {ref}") from exc
    if not isinstance(current, dict):
        raise SchemaError(f"schema reference does not resolve to an object: {ref}")
    return current


def validate_schema(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str = "$",
) -> None:
    if "$ref" in schema:
        validate_schema(value, resolve_ref(root_schema, schema["$ref"]), root_schema, path)
        return

    for index, subschema in enumerate(schema.get("allOf", [])):
        validate_schema(value, subschema, root_schema, f"{path}<allOf:{index}>")

    if "anyOf" in schema:
        failures = []
        for subschema in schema["anyOf"]:
            try:
                validate_schema(value, subschema, root_schema, path)
                break
            except SchemaError as exc:
                failures.append(str(exc))
        else:
            raise SchemaError(f"{path}: no anyOf branch matched: {failures}")

    if "oneOf" in schema:
        matches = 0
        for subschema in schema["oneOf"]:
            try:
                validate_schema(value, subschema, root_schema, path)
                matches += 1
            except SchemaError:
                pass
        if matches != 1:
            raise SchemaError(f"{path}: expected one oneOf match, got {matches}")

    if "const" in schema and value != schema["const"]:
        raise SchemaError(f"{path}: expected {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{path}: {value!r} is not in {schema['enum']!r}")

    expected = schema.get("type")
    if expected is not None:
        allowed = [expected] if isinstance(expected, str) else expected
        if not any(json_type_matches(value, item) for item in allowed):
            raise SchemaError(
                f"{path}: expected type {expected!r}, got {type(value).__name__}"
            )

    if isinstance(value, dict):
        missing = [name for name in schema.get("required", []) if name not in value]
        if missing:
            raise SchemaError(f"{path}: missing required properties {missing!r}")
        properties = schema.get("properties", {})
        for name, child in value.items():
            if name in properties:
                validate_schema(child, properties[name], root_schema, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                raise SchemaError(f"{path}: unexpected property {name!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate_schema(
                    child,
                    schema["additionalProperties"],
                    root_schema,
                    f"{path}.{name}",
                )
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            raise SchemaError(f"{path}: too few properties")
        if "maxProperties" in schema and len(value) > schema["maxProperties"]:
            raise SchemaError(f"{path}: too many properties")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise SchemaError(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaError(f"{path}: too many items")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True) for item in value]
            if len(encoded) != len(set(encoded)):
                raise SchemaError(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, child in enumerate(value):
                validate_schema(child, item_schema, root_schema, f"{path}[{index}]")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise SchemaError(f"{path}: string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaError(f"{path}: string is longer than {schema['maxLength']}")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value)
            except re.error as exc:
                raise SchemaError(f"{path}: unsupported regex") from exc
            if matched is None:
                raise SchemaError(
                    f"{path}: {value!r} does not match {schema['pattern']!r}"
                )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaError(f"{path}: {value} is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaError(f"{path}: {value} is above maximum {schema['maximum']}")


def validate_protected_inputs() -> None:
    for relative, expected in PROTECTED_HASHES.items():
        path = ROOT / relative
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError as exc:
            raise VerificationError(f"protected input is missing: {relative}") from exc
        check(actual == expected, f"protected input changed: {relative}")


def validate_plan_schema(plan: dict[str, Any]) -> None:
    schema = read_json(PLAN_SCHEMA_PATH)
    validate_schema(plan, schema, schema)

    installer = read_json(OPENAPI_PATH)
    check(
        installer.get("info", {}).get("version") == "9.1.0.0",
        "vendored installer specification is not version 9.1.0.0",
    )
    try:
        sddc_schema = installer["components"]["schemas"]["SddcSpec"]
        target_spec = plan["targetSddcSpec"]
    except KeyError as exc:
        raise SchemaError(f"cannot locate installer SddcSpec input: {exc}") from exc
    validate_schema(target_spec, sddc_schema, installer, "$.targetSddcSpec")


def expected_topology_assessments(
    inventory: dict[str, Any], compatibility: dict[str, Any]
) -> tuple[int, float, list[dict[str, Any]]]:
    hosts = [item for item in inventory["components"] if item["type"] == "ESXI"]
    minimum = compatibility["licensing"]["minimumCoresPerSocket"]
    required_cores = sum(
        host["cpuSockets"] * max(host["coresPerSocket"], minimum) for host in hosts
    )
    raw_vsan = sum(float(host["vsanRawTiB"]) for host in hosts)
    assessments = []
    for topology in compatibility["topologies"]:
        projected_cores = required_cores * topology["hostCapacityMultiplier"]
        projected_raw = raw_vsan * topology["vsanCapacityMultiplier"]
        included = (
            projected_cores
            * compatibility["licensing"]["vcfIncludedVsanTiBPerCore"]
        )
        licensed_raw = (
            math.ceil(projected_raw)
            if compatibility["licensing"]["vsanCapacityRoundToWholeTiB"]
            else projected_raw
        )
        required_addon = max(0, math.ceil(licensed_raw - included))
        cores_pass = bool(inventory["entitlements"]["version9Eligible"]) and (
            inventory["entitlements"]["foundationCores"] >= projected_cores
        )
        addon_pass = inventory["entitlements"]["vsanAddonTiB"] >= required_addon
        eligible = cores_pass and addon_pass
        assessments.append(
            {
                "topologyId": topology["id"],
                "technicalCompatibility": topology["technicalCompatibility"],
                "entitlementStatus": "ELIGIBLE" if eligible else "INELIGIBLE",
                "selected": False,
                "gates": [
                    {
                        "id": "FOUNDATION_CORES",
                        "required": projected_cores,
                        "available": inventory["entitlements"]["foundationCores"],
                        "passed": cores_pass,
                    },
                    {
                        "id": "VSAN_ADDON_TIB",
                        "required": required_addon,
                        "available": inventory["entitlements"]["vsanAddonTiB"],
                        "passed": addon_pass,
                    },
                ],
            }
        )
    entitled = [
        item
        for item in assessments
        if item["technicalCompatibility"]
        and item["entitlementStatus"] == "ELIGIBLE"
    ]
    check(len(entitled) == 1, "pinned inputs do not identify exactly one entitled topology")
    selected = entitled[0]["topologyId"]
    for item in assessments:
        item["selected"] = item["topologyId"] == selected
    return required_cores, raw_vsan, assessments


def validate_plan_semantics(
    plan: dict[str, Any],
    inventory: dict[str, Any],
    compatibility: dict[str, Any],
) -> None:
    check(plan["estateId"] == inventory["estateId"], "estateId differs from inventory")
    check(
        plan["sourceVcfVersion"] == inventory["vcfVersion"],
        "source VCF version differs from inventory",
    )
    check(
        plan["targetVcfVersion"]
        == inventory["targetVersion"]
        == compatibility["targetVcfVersion"],
        "target VCF version differs from pinned inputs",
    )

    supported = [
        route
        for route in compatibility["upgradePaths"]
        if route["supported"]
        and route["from"] == inventory["vcfVersion"]
        and route["to"] == inventory["targetVersion"]
    ]
    check(len(supported) == 1, "pinned snapshot lacks one supported estate route")
    route = supported[0]
    expected_path = [route["from"], *route["allowedIntermediates"], route["to"]]
    check(plan["upgradePath"] == expected_path, "plan contains an unsupported VCF hop")
    blocked = set(route["blockedIntermediatesWhenNsxBuildAtLeast"]["versions"])
    check(not blocked.intersection(plan["upgradePath"]), "plan contains a blocked NSX hop")

    required_cores, raw_vsan, expected_decisions = expected_topology_assessments(
        inventory, compatibility
    )
    actual_decisions = {
        item["topologyId"]: item for item in plan["topologyDecisions"]
    }
    expected_by_id = {item["topologyId"]: item for item in expected_decisions}
    check(
        len(actual_decisions) == len(plan["topologyDecisions"])
        and actual_decisions == expected_by_id,
        "topology compatibility or entitlement decision is incorrect",
    )
    selected = [item for item in expected_decisions if item["selected"]][0]
    check(
        plan["selectedTopologyId"] == selected["topologyId"],
        "selected topology is not the uniquely entitled topology",
    )
    rejected = [item for item in expected_decisions if not item["selected"]]
    check(
        rejected
        and all(item["technicalCompatibility"] for item in rejected)
        and any(item["entitlementStatus"] == "INELIGIBLE" for item in rejected),
        "the technically compatible but unentitled topology was not rejected",
    )

    included = (
        required_cores * compatibility["licensing"]["vcfIncludedVsanTiBPerCore"]
    )
    licensed_raw = (
        math.ceil(raw_vsan)
        if compatibility["licensing"]["vsanCapacityRoundToWholeTiB"]
        else raw_vsan
    )
    expected_license = {
        "requiredFoundationCores": required_cores,
        "entitledFoundationCores": inventory["entitlements"]["foundationCores"],
        "rawVsanTiB": raw_vsan,
        "includedVsanTiB": included,
        "requiredVsanAddonTiB": max(0, math.ceil(licensed_raw - included)),
        "availableVsanAddonTiB": inventory["entitlements"]["vsanAddonTiB"],
    }
    check(plan["licenseAssessment"] == expected_license, "license assessment is incorrect")

    components = {item["id"]: item for item in inventory["components"]}
    check(len(components) == len(inventory["components"]), "fixture has duplicate component ids")
    steps = plan["steps"]
    check(len(steps) == len(components), "plan does not name every component exactly once")
    check(
        [step["order"] for step in steps] == list(range(1, len(steps) + 1)),
        "component action order is not contiguous",
    )
    step_ids = [step["componentId"] for step in steps]
    check(len(step_ids) == len(set(step_ids)), "plan contains a duplicate component")
    check(set(step_ids) == set(components), "plan omits or invents an inventory component")

    position_by_type: dict[str, list[int]] = {}
    for step in steps:
        component = components[step["componentId"]]
        component_type = component["type"]
        check(step["componentType"] == component_type, f"wrong type for {component['id']}")
        check(step["fromVersion"] == component["version"], f"wrong current version for {component['id']}")
        check(
            step["targetVersion"] == compatibility["targetBom"][component_type],
            f"wrong target version for {component['id']}",
        )
        check(step["action"] == "UPGRADE", f"wrong action for {component['id']}")
        check(
            step["gates"] == compatibility["gateRules"][component_type],
            f"wrong gates for {component['id']}",
        )
        position_by_type.setdefault(component_type, []).append(step["order"])
    for before, after in compatibility["orderConstraints"]:
        check(
            max(position_by_type[before]) < min(position_by_type[after]),
            f"component ordering violates {before} before {after}",
        )

    selected_topology = next(
        item
        for item in compatibility["topologies"]
        if item["id"] == plan["selectedTopologyId"]
    )
    target = plan["targetSddcSpec"]
    check(target["sddcId"] == inventory["sddcId"], "wrong target SDDC id")
    check(target.get("workflowType") == "VCF", "target workflow must be VCF")
    check(target.get("version") == inventory["targetVersion"], "wrong target spec version")
    check(
        target["vcenterSpec"]["vcenterHostname"] == inventory["vcenter"]["hostname"],
        "wrong existing vCenter hostname",
    )
    check(
        target["vcenterSpec"].get("useExistingDeployment") is True,
        "target spec must reuse the brownfield vCenter",
    )
    check(
        target["vcenterSpec"].get("sslThumbprint")
        == inventory["vcenter"]["sslThumbprint"],
        "wrong vCenter certificate thumbprint",
    )
    expected_networks = [
        {
            "networkType": item["networkType"],
            "vlanId": item["vlanId"],
            "subnet": item["subnet"],
            "gateway": item["gateway"],
            "mtu": item["mtu"],
        }
        for item in inventory["networks"]
    ]
    check(target["networkSpecs"] == expected_networks, "target networks differ from inventory")
    check(
        target["dnsSpec"]
        == {
            "subdomain": inventory["site"]["domain"],
            "nameservers": inventory["site"]["dnsServers"],
        },
        "target DNS spec differs from inventory",
    )
    check(target.get("ntpServers") == inventory["site"]["ntpServers"], "wrong NTP servers")
    check(
        target.get("datastoreSpec", {}).get("existingDatastoreName")
        == selected_topology["existingDatastoreName"],
        "wrong datastore for selected topology",
    )


def validate_research() -> None:
    try:
        text = RESEARCH_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise VerificationError("research/consulted-sources.md is missing") from exc

    record_pattern = re.compile(
        r"(?ms)^-\s+Title:\s*(?P<title>[^\r\n]+)\r?\n"
        r"\s+Publisher:\s*(?P<publisher>[^\r\n]+)\r?\n"
        r"\s+Accessed:\s*(?P<accessed>\d{4}-\d{2}-\d{2})\r?\n"
        r"\s+URL:\s*(?P<url>https://\S+)\r?\n"
        r"\s+Decision:\s*(?P<decision>[^\r\n]+)"
    )
    records = list(record_pattern.finditer(text))
    check(len(records) >= 2, "research record needs at least two complete source entries")
    urls: set[str] = set()
    searchable = []
    for index, match in enumerate(records, start=1):
        values = {name: value.strip() for name, value in match.groupdict().items()}
        check(all(values.values()), f"research source {index} has an empty field")
        try:
            dt.date.fromisoformat(values["accessed"])
        except ValueError as exc:
            raise VerificationError(f"research source {index} has an invalid access date") from exc
        try:
            parsed = urlparse(values["url"])
            host = (parsed.hostname or "").lower()
        except ValueError as exc:
            raise VerificationError(f"research source {index} has a malformed URL") from exc
        check(parsed.scheme == "https", f"research source {index} must use HTTPS")
        check(
            host == "broadcom.com" or host.endswith(".broadcom.com"),
            f"research source {index} is not Broadcom-published",
        )
        check(parsed.path not in ("", "/") or host == "interopmatrix.broadcom.com", f"research source {index} URL is not specific")
        normalized = values["url"].rstrip("/").lower()
        check(normalized not in urls, f"duplicate research URL in source {index}")
        urls.add(normalized)
        searchable.extend((values["title"].lower(), values["decision"].lower()))

    combined = " ".join(searchable)
    check(
        "interoperab" in combined or "compatib" in combined,
        "research does not record a compatibility/interoperability decision",
    )
    check("upgrade" in combined or "hop" in combined, "research does not record an upgrade-path decision")
    check(
        "bill of materials" in combined or "bom" in combined or "component version" in combined or "correlat" in combined,
        "research does not record a bill-of-materials decision",
    )
    check(
        "licens" in combined or "entitle" in combined or "core" in combined,
        "research does not record a licensing/entitlement decision",
    )


def run_process(args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            args,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise VerificationError(f"required command is unavailable: {args[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise VerificationError(f"command timed out: {args[0]}") from exc
    check(
        result.returncode == 0,
        f"command failed ({' '.join(args[:3])}): {result.stderr.strip() or result.stdout.strip()}",
    )
    return result


def inspect_module() -> None:
    check(MANIFEST_PATH.is_file(), "PowerShell module manifest is missing")
    check(IMPLEMENTATION_PATH.is_file(), "PowerShell module implementation is missing")
    vendored = {
        path.relative_to(MODULE_DIR).as_posix()
        for path in MODULE_DIR.rglob("*")
        if path.is_file()
        and (
            path.name.lower().startswith("vmware.sdk.vcf")
            or path.suffix.lower() == ".dll"
        )
    }
    check(not vendored, f"module directory contains vendored SDK files: {sorted(vendored)}")

    inspection_script = r'''
param([string] $ManifestPath, [string] $ImplementationPath)
$ErrorActionPreference = 'Stop'
$manifest = Import-PowerShellDataFile -LiteralPath $ManifestPath
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ImplementationPath, [ref]$tokens, [ref]$errors)
if ($errors.Count -gt 0) { throw ($errors.Message -join '; ') }
$functions = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true))
$functionInfo = [ordered]@{}
foreach ($function in $functions) {
    $functionInfo[$function.Name] = @($function.Body.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
}
$commands = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true) | ForEach-Object { $_.GetCommandName() })
$exportFunction = $functions | Where-Object Name -CEQ 'Export-VcfEstateInventory'
$hasFinally = $false
if ($null -ne $exportFunction) {
    $hasFinally = @($exportFunction.Body.FindAll({ param($n) $n -is [System.Management.Automation.Language.TryStatementAst] -and $null -ne $n.Finally }, $true)).Count -gt 0
}
$required = @(foreach ($entry in @($manifest.RequiredModules)) { if ($entry -is [string]) { $entry } else { $entry.ModuleName } })
[ordered]@{
    rootModule = $manifest.RootModule
    requiredModules = $required
    functionsToExport = @($manifest.FunctionsToExport)
    functionParameters = $functionInfo
    commands = $commands
    exportHasFinally = $hasFinally
} | ConvertTo-Json -Depth 20 -Compress
'''
    result = run_process(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-CommandWithArgs",
            inspection_script,
            str(MANIFEST_PATH),
            str(IMPLEMENTATION_PATH),
        ]
    )
    try:
        details = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationError(f"could not inspect PowerShell module: {result.stdout}") from exc

    check(details["rootModule"] == IMPLEMENTATION_PATH.name, "manifest RootModule is wrong")
    required = set(details["requiredModules"])
    check(
        {"VMware.Sdk.Vcf.SddcManager", "VMware.Sdk.Vcf.Installer"} <= required,
        "manifest must require both VMware VCF SDK modules",
    )
    exports = set(details["functionsToExport"])
    check(
        {"Export-VcfEstateInventory", "New-VcfMigrationPlan"} <= exports,
        "manifest does not export both required commands",
    )
    parameters = details["functionParameters"]
    check(
        {"Server", "Credential", "EstateId", "Path"}
        <= set(parameters.get("Export-VcfEstateInventory", [])),
        "Export-VcfEstateInventory has the wrong interface",
    )
    check(
        {"InventoryPath", "CompatibilityPath", "OutputPath"}
        <= set(parameters.get("New-VcfMigrationPlan", [])),
        "New-VcfMigrationPlan has the wrong interface",
    )
    check(details["exportHasFinally"], "live discovery must disconnect in a finally block")

    command_names = {name.split("\\")[-1] for name in details["commands"] if name}
    required_commands = {
        "Connect-VcfSddcManagerServer",
        "Disconnect-VcfSddcManagerServer",
        "Invoke-VcfGetSddcManager",
        "Invoke-VcfGetDomains",
        "Invoke-VcfGetVcenters",
        "Invoke-VcfGetNsxClusters",
        "Invoke-VcfGetHosts",
        "Invoke-VcfGetSystemLicensingInfo",
        "Invoke-VcfGetReleases",
        "Invoke-VcfGetCompatibilityMatrices",
        "Initialize-VcfInstallerSddcVcenterSpec",
        "Initialize-VcfInstallerDnsSpec",
        "Initialize-VcfInstallerSddcNetworkSpec",
        "Initialize-VcfInstallerSddcDatastoreSpec",
        "Initialize-VcfInstallerSddcSpec",
    }
    missing = required_commands - command_names
    check(not missing, f"module does not use required generated SDK commands: {sorted(missing)}")
    forbidden = {"Install-Module", "Save-Module", "Install-PSResource", "Save-PSResource"}
    check(not (forbidden & command_names), "module must not install or vendor VMware modules")


PLANNER_HARNESS = r'''
param(
    [string] $ModulePath,
    [string] $InventoryPath,
    [string] $CompatibilityPath,
    [string] $OutputPath
)
$ErrorActionPreference = 'Stop'
Import-Module -Name $ModulePath -Force
$exported = @(Get-Command -Module VcfBrownfieldArchitecture -CommandType Function | Select-Object -ExpandProperty Name)
$null = New-VcfMigrationPlan -InventoryPath $InventoryPath -CompatibilityPath $CompatibilityPath -OutputPath $OutputPath
$exported | ConvertTo-Json -Compress
'''


def invoke_planner(inventory: Path, compatibility: Path, output: Path) -> set[str]:
    result = run_process(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-CommandWithArgs",
            PLANNER_HARNESS,
            str(IMPLEMENTATION_PATH),
            str(inventory),
            str(compatibility),
            str(output),
        ]
    )
    try:
        exported = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationError(f"planner harness returned invalid output: {result.stdout}") from exc
    return {exported} if isinstance(exported, str) else set(exported)


def exercise_planner(
    committed: dict[str, Any],
    inventory: dict[str, Any],
    compatibility: dict[str, Any],
) -> None:
    with tempfile.TemporaryDirectory(prefix="vcfarch-verify-") as temp_name:
        temp = Path(temp_name)
        first = temp / "first.json"
        second = temp / "second.json"
        exported = invoke_planner(INVENTORY_PATH, COMPATIBILITY_PATH, first)
        invoke_planner(INVENTORY_PATH, COMPATIBILITY_PATH, second)
        check(
            {"Export-VcfEstateInventory", "New-VcfMigrationPlan"} <= exported,
            "implementation does not export both required functions",
        )
        generated = read_json(first)
        check(generated == committed, "module does not reproduce out/migration-plan.json")
        check(first.read_bytes() == second.read_bytes(), "module output is not byte-deterministic")

        variant_inventory = copy.deepcopy(inventory)
        variant_inventory["estateId"] = "chi01-vcf-variant"
        variant_inventory["components"][0]["version"] = "8.0.3-variant"
        variant_compatibility = copy.deepcopy(compatibility)
        variant_compatibility["targetBom"]["VSAN"] = "9.1.0.0-variant"
        variant_compatibility["gateRules"]["VSAN"].append("VARIANT_GATE")
        variant_inventory_path = temp / "variant-inventory.json"
        variant_compatibility_path = temp / "variant-compatibility.json"
        variant_output = temp / "variant-output.json"
        variant_inventory_path.write_text(json.dumps(variant_inventory), encoding="utf-8")
        variant_compatibility_path.write_text(json.dumps(variant_compatibility), encoding="utf-8")
        invoke_planner(variant_inventory_path, variant_compatibility_path, variant_output)
        variant = read_json(variant_output)
        check(variant["estateId"] == "chi01-vcf-variant", "planner ignores inventory estateId")
        vsan_step = next(item for item in variant["steps"] if item["componentType"] == "VSAN")
        check(vsan_step["fromVersion"] == "8.0.3-variant", "planner ignores component inventory data")
        check(vsan_step["targetVersion"] == "9.1.0.0-variant", "planner ignores pinned target BOM")
        check(vsan_step["gates"][-1] == "VARIANT_GATE", "planner ignores pinned gate rules")


def main() -> None:
    validate_protected_inputs()
    plan = read_json(PLAN_PATH)
    check(isinstance(plan, dict), "migration plan root must be an object")
    inventory = read_json(INVENTORY_PATH)
    compatibility = read_json(COMPATIBILITY_PATH)
    validate_plan_schema(plan)
    validate_plan_semantics(plan, inventory, compatibility)
    validate_research()
    inspect_module()
    exercise_planner(plan, inventory, compatibility)
    print("VCF brownfield architecture verification passed.")


if __name__ == "__main__":
    try:
        main()
    except (VerificationError, KeyError, TypeError, ValueError) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
